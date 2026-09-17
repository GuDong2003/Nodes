# -*- coding: utf-8 -*-
"""Live proxy pool: 8 slots per healthy ProxyScrape account."""

import hashlib
import os
import re
from pathlib import Path
from urllib.parse import quote


DEFAULT_TARGET_SLOTS = 80
DEFAULT_SLOTS_PER_ACCOUNT = 8
DEFAULT_MIN_BANDWIDTH = 100 * 1024 * 1024
DEFAULT_MAX_REGISTER = 5
DEFAULT_LOOP_SECONDS = 120
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 8970
DEFAULT_PLATFORM = "Default"

_PROXY_LINE = re.compile(
    r"^(?:https?://)?([^:@/]+):([^@/]+)@(\[[^\]]+\]:\d+|[^/\s]+)",
    re.I,
)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def pool_settings(config):
    data = config if isinstance(config, dict) else {}
    slots = max(1, min(8, _as_int(data.get("pool_slots_per_account"), DEFAULT_SLOTS_PER_ACCOUNT)))
    target = max(slots, min(800, _as_int(data.get("pool_target_slots"), DEFAULT_TARGET_SLOTS)))
    min_bandwidth = max(0, _as_int(data.get("pool_min_bandwidth"), DEFAULT_MIN_BANDWIDTH))
    max_register = max(1, min(5, _as_int(data.get("pool_max_register_per_round"), DEFAULT_MAX_REGISTER)))
    loop_seconds = max(30, min(3600, _as_int(data.get("pool_loop_seconds"), DEFAULT_LOOP_SECONDS)))
    auto_register = True if "pool_auto_register" not in data else _as_bool(data.get("pool_auto_register"))
    return {
        "target_slots": target,
        "slots_per_account": slots,
        "min_bandwidth": min_bandwidth,
        "max_register_per_round": max_register,
        "loop_seconds": loop_seconds,
        "auto_register": auto_register,
        "gateway_host": str(data.get("resin_gateway_host") or DEFAULT_GATEWAY_HOST).strip() or DEFAULT_GATEWAY_HOST,
        "gateway_port": max(1, min(65535, _as_int(data.get("resin_gateway_port"), DEFAULT_GATEWAY_PORT))),
        "gateway_platform": str(data.get("resin_gateway_platform") or DEFAULT_PLATFORM).strip() or DEFAULT_PLATFORM,
    }


def harvest_ips_by_username(node_dir):
    mapping = {}
    root = Path(node_dir)
    if not root.exists():
        return mapping
    for path in sorted(root.glob("*.txt")):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for raw in text.splitlines():
            match = _PROXY_LINE.match(raw.strip())
            if not match:
                continue
            user, _password, host = match.group(1), match.group(2), match.group(3)
            bucket = mapping.setdefault(user, {"seen": set(), "hosts": []})
            if host in bucket["seen"]:
                continue
            bucket["seen"].add(host)
            bucket["hosts"].append(host)
    return {user: value["hosts"] for user, value in mapping.items()}


def normalize_hosts(raw):
    hosts = []
    seen = set()
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        match = _PROXY_LINE.match(text)
        if match:
            text = match.group(3)
        text = text.split("#", 1)[0].strip().strip("/")
        if "://" in text:
            text = text.split("://", 1)[1]
        if "@" in text:
            text = text.rsplit("@", 1)[-1]
        if ":" not in text:
            continue
        if text in seen:
            continue
        seen.add(text)
        hosts.append(text)
    return hosts


def pick_slots(hosts, count, seed):
    values = list(hosts)
    if len(values) <= count:
        return values
    return sorted(
        values,
        key=lambda host: hashlib.sha256(f"{seed}|{host}".encode("utf-8")).hexdigest(),
    )[:count]


def account_is_live(record, now, min_bandwidth):
    user = str((record or {}).get("proxy_username") or "").strip()
    password = str((record or {}).get("proxy_password") or "").strip()
    if not user or not password:
        return False
    expiry = record.get("expiration_time") or record.get("expiry")
    try:
        expiry = int(expiry) if expiry else None
    except (TypeError, ValueError):
        expiry = None
    if expiry and expiry <= now:
        return False
    remaining = record.get("bandwidth_remaining")
    try:
        remaining = int(remaining) if remaining is not None else None
    except (TypeError, ValueError):
        remaining = None
    if remaining is not None and remaining < min_bandwidth:
        return False
    return True


def live_entries(records, node_dir, settings, now):
    harvested = harvest_ips_by_username(node_dir)
    slots_per_account = int(settings["slots_per_account"])
    min_bandwidth = int(settings["min_bandwidth"])
    entries = []
    for record in records or []:
        if not account_is_live(record, now, min_bandwidth):
            continue
        user = str(record.get("proxy_username") or "").strip()
        password = str(record.get("proxy_password") or "").strip()
        email = str(record.get("email") or "").strip()
        hosts = normalize_hosts(record.get("proxy_ips")) or harvested.get(user) or []
        slots = pick_slots(hosts, slots_per_account, email or user)
        if not slots:
            continue
        entries.append({
            "email": email,
            "proxy_username": user,
            "proxy_password": password,
            "slots": slots,
        })
    return entries


def capacity(entries, settings):
    live_slots = sum(len(item["slots"]) for item in entries)
    target = int(settings["target_slots"])
    per_account = int(settings["slots_per_account"])
    shortage = max(0, target - live_slots)
    needed_accounts = (shortage + per_account - 1) // per_account if shortage else 0
    return {
        "live_accounts": len(entries),
        "live_slots": live_slots,
        "target_slots": target,
        "slots_per_account": per_account,
        "shortage_slots": shortage,
        "needed_accounts": needed_accounts,
        "max_register_per_round": int(settings["max_register_per_round"]),
        "auto_register": bool(settings["auto_register"]),
        "min_bandwidth": int(settings["min_bandwidth"]),
    }


def format_proxy_lines(entries, format_url):
    lines = []
    for item in entries:
        for host in item["slots"]:
            lines.append(format_url(item["proxy_username"], item["proxy_password"], host))
    return lines


def gateway_identity(index, auth_version, platform, token):
    account = f"n{index:02d}"
    version = str(auth_version or "V1").strip().upper()
    if version in {"V1", "V1.0"}:
        return f"{platform}.{account}", token
    return token, f"{platform}:{account}"


def gpt_gateway_lines(count, token, host, port, auth_version, platform):
    lines = []
    total = max(0, int(count))
    for index in range(1, total + 1):
        user, password = gateway_identity(index, auth_version, platform, token)
        lines.append(
            f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{int(port)}"
        )
    return lines


def env_file_map(path):
    values = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resin_auth(config=None):
    data = config if isinstance(config, dict) else {}
    env_map = env_file_map(os.environ.get("RESIN_ENV_FILE", "/etc/resin.env"))
    token = (
        str(os.environ.get("RESIN_PROXY_TOKEN") or "").strip()
        or str(data.get("resin_proxy_token") or "").strip()
        or str(env_map.get("RESIN_PROXY_TOKEN") or "").strip()
    )
    auth_version = (
        str(os.environ.get("RESIN_AUTH_VERSION") or "").strip()
        or str(data.get("resin_auth_version") or "").strip()
        or str(env_map.get("RESIN_AUTH_VERSION") or "").strip()
        or "V1"
    )
    return token, auth_version
