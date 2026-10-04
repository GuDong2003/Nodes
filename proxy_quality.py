# -*- coding: utf-8 -*-
"""Opt-in proxy country/latency/connectivity gate, adapted from upstream f140ea8.

Reports contain endpoints without userinfo. Accepted URL return values remain
usable authenticated URLs. No token minting or publish actions are performed.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

import requests

from platform_store import _atomic_write, redact

DEFAULT_MAX_LATENCY_MS = 3000
DEFAULT_TIMEOUT_SEC = 12.0
DEFAULT_WORKERS = 8
DEFAULT_CACHE_TTL_SEC = 600
DEFAULT_GEO_URL = 'http://ip-api.com/json/?fields=status,message,country,countryCode,query'
DEFAULT_ARP_PROBE_URL = 'https://cp.cloudflare.com/generate_204'
DEFAULT_EXCLUDE_COUNTRIES = ()
_CACHE_MAX = 5000
_cache_lock = threading.RLock()
_memory_cache = {}
_FIELDS = ('identity', 'ok', 'latency_ms', 'country', 'country_code', 'egress_ip',
           'arp_ok', 'arp_status', 'reason', 'checked_at')


def _as_bool(value, default=False):
    return default if value is None else str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _number(value, default, minimum, maximum, integer=False):
    try:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('non-finite number')
    except (TypeError, ValueError, OverflowError):
        number = default
    number = min(maximum, max(minimum, number))
    return int(number) if integer else number


def quality_settings(config=None):
    """Normalize flat upstream configuration keys, using neutral defaults."""
    data = config if isinstance(config, dict) else {}
    raw = data.get('proxy_exclude_countries', [])
    if isinstance(raw, str):
        raw = raw.replace(';', ',').split(',')
    countries = sorted({str(item).strip().upper() for item in raw if str(item).strip()}) if isinstance(raw, (list, tuple, set)) else []
    return {
        'enabled': _as_bool(data.get('proxy_quality_enabled')),
        'max_latency_ms': _number(data.get('proxy_max_latency_ms'), 3000, 1, 120000, True),
        'exclude_countries': countries,
        'arp_check_enabled': _as_bool(data.get('proxy_arp_check_enabled')),
        'arp_probe_url': str(data.get('proxy_arp_probe_url') or DEFAULT_ARP_PROBE_URL).strip(),
        'geo_url': str(data.get('proxy_geo_url') or DEFAULT_GEO_URL).strip(),
        'workers': _number(data.get('proxy_quality_workers'), 8, 1, 32, True),
        'cache_ttl_sec': _number(data.get('proxy_quality_cache_ttl_sec'), 600, 0, 86400, True),
        'timeout_sec': _number(data.get('proxy_quality_timeout_sec'), 12, 1, 60),
    }


def _coerce_settings(settings=None):
    data = settings if isinstance(settings, dict) else {}
    if 'enabled' not in data:
        return quality_settings(data)
    mapping = {'enabled': 'proxy_quality_enabled', 'max_latency_ms': 'proxy_max_latency_ms',
               'exclude_countries': 'proxy_exclude_countries', 'arp_check_enabled': 'proxy_arp_check_enabled',
               'arp_probe_url': 'proxy_arp_probe_url', 'geo_url': 'proxy_geo_url',
               'workers': 'proxy_quality_workers', 'cache_ttl_sec': 'proxy_quality_cache_ttl_sec',
               'timeout_sec': 'proxy_quality_timeout_sec'}
    return quality_settings({mapping[key]: value for key, value in data.items() if key in mapping})


def normalize_proxy_url(raw, default_scheme='http'):
    text = str(raw or '').strip()
    if not text or text.startswith('#'):
        return ''
    return text if '://' in text else f'{default_scheme}://{text}'


def _valid_proxy(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme.lower() in {'http', 'https', 'socks5', 'socks5h'}
                and bool(parsed.hostname) and parsed.port is not None and 1 <= parsed.port <= 65535
                and not any(char.isspace() for char in url)
                and parsed.path in {'', '/'} and not parsed.query and not parsed.fragment)
    except ValueError:
        return False


def proxy_identity(proxy_url):
    """Return host:port without authentication (bracket IPv6 addresses)."""
    try:
        parsed = urlsplit(normalize_proxy_url(proxy_url))
        if parsed.hostname and parsed.port is not None:
            host = parsed.hostname
            return f'[{host}]:{parsed.port}' if ':' in host else f'{host}:{parsed.port}'
    except ValueError:
        pass
    return '<invalid>'


def is_excluded_country(country_code, country_name, exclude_countries):
    blocked = {str(item).upper() for item in exclude_countries or []}
    code = str(country_code or '').strip().upper()
    name = str(country_name or '').strip().lower()
    return code in blocked or ('US' in blocked and name in {'us', 'usa', 'united states', 'united states of america'})


def _row(url, reason='not probed yet'):
    return {'identity': proxy_identity(url), 'ok': None, 'latency_ms': None,
            'country': '', 'country_code': '', 'egress_ip': '', 'arp_ok': None,
            'arp_status': None, 'reason': reason, 'checked_at': None}


def _session_for_proxy(proxy_url):
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'User-Agent': 'nodes-proxy-quality/1.0'})
    session.proxies = {'http': proxy_url, 'https': proxy_url}
    return session


def _valid_target(url):
    try:
        parsed = urlsplit(url)
        return parsed.scheme in {'http', 'https'} and bool(parsed.hostname) and not parsed.username and not parsed.password and (parsed.port is None or 1 <= parsed.port <= 65535)
    except ValueError:
        return False


def probe_proxy(proxy_url, settings=None):
    """Run geo/latency and optional generic target GET through one proxy."""
    cfg = _coerce_settings(settings)
    url = normalize_proxy_url(proxy_url)
    result = _row(url)
    if not cfg['enabled']:
        result['reason'] = 'quality filter disabled'
        return result
    result.update(ok=False, checked_at=time.time())
    if not _valid_proxy(url):
        result['reason'] = 'invalid proxy URL'
        return result
    if not _valid_target(cfg['geo_url']) or cfg['arp_check_enabled'] and not _valid_target(cfg['arp_probe_url']):
        result['reason'] = 'invalid probe URL'
        return result
    session = None
    started = time.monotonic()
    try:
        session = _session_for_proxy(url)
        geo = session.get(cfg['geo_url'], timeout=cfg['timeout_sec'])
        result['latency_ms'] = int((time.monotonic() - started) * 1000)
        if geo.status_code != 200:
            result['reason'] = f'geo probe failed HTTP {int(geo.status_code)}'
            return result
        try:
            payload = geo.json()
        except ValueError:
            payload = None
        if not isinstance(payload, dict) or str(payload.get('status') or '').lower() == 'fail':
            result['reason'] = 'invalid geo response'
            return result
        code = str(payload.get('countryCode') or payload.get('country_code') or '').upper()
        ip = str(payload.get('query') or payload.get('ip') or '')
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            result['reason'] = 'invalid geo response'
            return result
        if not re.fullmatch(r'[A-Z]{2}', code):
            result['reason'] = 'invalid geo response'
            return result
        result.update(country=redact(str(payload.get('country') or '')[:100]), country_code=code, egress_ip=ip)
        if is_excluded_country(code, result['country'], cfg['exclude_countries']):
            result['reason'] = f'excluded country {code}'
            return result
        if result['latency_ms'] > cfg['max_latency_ms']:
            result['reason'] = f"latency {result['latency_ms']}ms > {cfg['max_latency_ms']}ms"
            return result
        if cfg['arp_check_enabled']:
            target_started = time.monotonic()
            response = session.get(cfg['arp_probe_url'], timeout=cfg['timeout_sec'], allow_redirects=True)
            result['latency_ms'] = max(result['latency_ms'], int((time.monotonic() - target_started) * 1000))
            result['arp_status'] = int(response.status_code)
            result['arp_ok'] = 200 <= result['arp_status'] < 300
            if not result['arp_ok']:
                result['reason'] = f"target probe failed HTTP {result['arp_status']}"
                return result
            if result['latency_ms'] > cfg['max_latency_ms']:
                result['reason'] = f"latency {result['latency_ms']}ms > {cfg['max_latency_ms']}ms"
                return result
        result.update(ok=True, reason='ok')
    except requests.RequestException as exc:
        result.update(latency_ms=int((time.monotonic() - started) * 1000),
                      reason=f'proxy probe failed ({type(exc).__name__})')
    finally:
        if session is not None:
            session.close()
    return result


def _cache_path(data_dir):
    return Path(data_dir) / 'proxy_quality_cache.json' if data_dir else None


def _namespace(data_dir):
    return str(Path(data_dir).resolve()) if data_dir else '<memory-only>'


def _cache_key(url, cfg):
    rules = {key: cfg[key] for key in ('max_latency_ms', 'exclude_countries', 'arp_check_enabled',
                                      'arp_probe_url', 'geo_url', 'timeout_sec')}
    return hashlib.sha256(json.dumps([url, rules], sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _public_result(row):
    return redact({key: row.get(key) for key in _FIELDS})


def _clean_cache(payload):
    if not isinstance(payload, dict):
        return {}
    return {key: _public_result(row) for key, row in payload.items()
            if isinstance(key, str) and re.fullmatch(r'[0-9a-f]{64}', key) and isinstance(row, dict)}


def load_cache(data_dir):
    path = _cache_path(data_dir)
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    return _clean_cache(payload)


def save_cache(data_dir, cache):
    path = _cache_path(data_dir)
    if path is not None:
        _atomic_write(path, json.dumps(_clean_cache(cache), ensure_ascii=False, allow_nan=False))


def _fresh(row, ttl):
    try:
        age = time.time() - float(row.get('checked_at'))
        return 0 <= age <= ttl and isinstance(row.get('ok'), bool)
    except (TypeError, ValueError, OverflowError):
        return False


def _timestamp(row):
    try:
        number = float(row.get('checked_at') or 0)
        return number if math.isfinite(number) else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _cached_rows(keys, data_dir, ttl):
    namespace = _namespace(data_dir)
    with _cache_lock:
        found = {key: dict(row) for key in keys
                 if (row := _memory_cache.get((namespace, key))) is not None and _fresh(row, ttl)}
        if len(found) != len(keys):
            disk = load_cache(data_dir)
            for key in keys:
                if key not in found and (row := disk.get(key)) is not None and _fresh(row, ttl):
                    found[key] = dict(row)
        # Reads intentionally do not populate or mutate cache.
        return found


def _cache_put_many(rows, data_dir):
    """Merge a complete probe batch under one lock and one atomic disk write."""
    namespace = _namespace(data_dir)
    items = {key: _public_result(row) for key, row in rows.items()}
    with _cache_lock:
        if data_dir:
            disk = load_cache(data_dir)
            disk.update(items)
            if len(disk) > _CACHE_MAX:
                disk = dict(sorted(disk.items(), key=lambda pair: _timestamp(pair[1]), reverse=True)[:4000])
            save_cache(data_dir, disk)
        _memory_cache.update({(namespace, key): item for key, item in items.items()})
        if len(_memory_cache) > _CACHE_MAX:
            oldest = sorted(_memory_cache, key=lambda key: _timestamp(_memory_cache[key]))
            for old in oldest[:len(_memory_cache) - 4000]:
                del _memory_cache[old]


def filter_proxies(proxy_urls, settings=None, data_dir=None, use_cache=True, probe_missing=True):
    """Return usable accepted URLs and ordered, credential-free quality results.

    Disabled means pass-through with all observations untested. A cache-only
    read excludes untested URLs from accepted URLs but never counts them failed.
    Dry-runs (use_cache=False) bypass all cache reads and writes.
    """
    cfg = _coerce_settings(settings)
    urls = list(dict.fromkeys(url for raw in proxy_urls or [] if (url := normalize_proxy_url(raw))))
    rows = [_row(url) for url in urls]
    if not cfg['enabled']:
        for row in rows:
            row['reason'] = 'quality filter disabled'
        return urls, {'scanned': len(urls), 'accepted': 0, 'rejected': 0,
                      'skipped_unprobed': len(urls), 'enabled': False, 'results': rows}
    pending = []
    keys = [_cache_key(url, cfg) for url in urls]
    cached_rows = _cached_rows(keys, data_dir, cfg['cache_ttl_sec']) if use_cache and urls else {}
    for index, (url, key) in enumerate(zip(urls, keys)):
        if key in cached_rows:
            rows[index] = cached_rows[key]
        elif probe_missing:
            pending.append((index, url, key))
    def check(entry):
        index, url, key = entry
        return index, key, _public_result(probe_proxy(url, cfg))
    if pending:
        probed = {}
        with ThreadPoolExecutor(max_workers=min(cfg['workers'], len(pending))) as executor:
            for index, key, row in executor.map(check, pending):
                rows[index] = row
                probed[key] = row
        if use_cache:
            _cache_put_many(probed, data_dir)
    accepted = [url for url, row in zip(urls, rows) if row.get('ok') is True]
    return accepted, {'scanned': len(urls), 'accepted': len(accepted),
                      'rejected': sum(row.get('ok') is False for row in rows),
                      'skipped_unprobed': sum(row.get('ok') is None for row in rows),
                      'enabled': True, 'results': rows}
