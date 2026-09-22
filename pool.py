# -*- coding: utf-8 -*-
"""Live proxy pool: export all distinct hosts of each eligible account."""

import json
import os
import re
from pathlib import Path
import base64
from urllib.parse import quote, quote_plus, urlsplit


DEFAULT_TARGET_SLOTS = 80
DEFAULT_EXPECTED_PROXIES_PER_ACCOUNT = 100
DEFAULT_MIN_BANDWIDTH = 100 * 1024 * 1024
DEFAULT_MAX_REGISTER = 5
DEFAULT_LOOP_SECONDS = 120
DEFAULT_GATEWAY_HOST = "127.0.0.1"
DEFAULT_GATEWAY_PORT = 8970
DEFAULT_PLATFORM = "Nodes"

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
    # Replenishment estimate only, never a limit on the exported hosts.
    # The old pool_slots_per_account sampling setting is deliberately ignored.
    expected = max(1, _as_int(data.get("pool_expected_proxies_per_account"), DEFAULT_EXPECTED_PROXIES_PER_ACCOUNT))
    target = max(1, min(800, _as_int(data.get("pool_target_slots"), DEFAULT_TARGET_SLOTS)))
    min_bandwidth = max(0, _as_int(data.get("pool_min_bandwidth"), DEFAULT_MIN_BANDWIDTH))
    max_register = max(1, min(5, _as_int(data.get("pool_max_register_per_round"), DEFAULT_MAX_REGISTER)))
    loop_seconds = max(30, min(3600, _as_int(data.get("pool_loop_seconds"), DEFAULT_LOOP_SECONDS)))
    auto_register = True if "pool_auto_register" not in data else _as_bool(data.get("pool_auto_register"))
    return {
        "target_slots": target,
        "slots_per_account": expected,  # Legacy response alias for the estimate.
        "expected_proxies_per_account": expected,
        "export_all": True,
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
    min_bandwidth = int(settings["min_bandwidth"])
    entries = []
    for record in records or []:
        if not account_is_live(record, now, min_bandwidth):
            continue
        user = str(record.get("proxy_username") or "").strip()
        password = str(record.get("proxy_password") or "").strip()
        email = str(record.get("email") or "").strip()
        hosts = normalize_hosts(record.get("proxy_ips")) or harvested.get(user) or []
        if not hosts:
            continue
        entries.append({
            "email": email,
            "proxy_username": user,
            "proxy_password": password,
            "slots": hosts,
        })
    return entries


def capacity(entries, settings):
    live_slots = sum(len(item["slots"]) for item in entries)
    target = int(settings["target_slots"])
    per_account = int(settings["expected_proxies_per_account"])
    shortage = max(0, target - live_slots)
    needed_accounts = (shortage + per_account - 1) // per_account if shortage else 0
    return {
        "live_accounts": len(entries),
        "live_slots": live_slots,
        "concurrent_slots": live_slots,  # Legacy node-count alias, not a concurrency guarantee.
        "target_slots": target,
        "slots_per_account": per_account,
        "expected_proxies_per_account": per_account,
        "export_all": True,
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


def _yaml_quote(value):
    return json.dumps(str(value), ensure_ascii=False)


def clash_yaml(count, token, host, port, auth_version, platform):
    total = max(0, int(count))
    names = []
    proxy_blocks = []
    for index in range(1, total + 1):
        user, password = gateway_identity(index, auth_version, platform, token)
        name = f"{platform}-{index:02d}"
        names.append(name)
        proxy_blocks.extend([
            f"  - name: {_yaml_quote(name)}",
            "    type: http",
            f"    server: {_yaml_quote(host)}",
            f"    port: {int(port)}",
            f"    username: {_yaml_quote(user)}",
            f"    password: {_yaml_quote(password)}",
        ])
    lines = [
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        "proxies:",
    ]
    if proxy_blocks:
        lines.extend(proxy_blocks)
    else:
        lines.append("  []")
    lines.append("proxy-groups:")
    if names:
        lines.extend([
            f"  - name: {_yaml_quote('AUTO')}",
            "    type: url-test",
            "    url: http://www.gstatic.com/generate_204",
            "    interval: 300",
            "    proxies:",
        ])
        lines.extend(f"      - {_yaml_quote(name)}" for name in names)
        lines.extend([
            f"  - name: {_yaml_quote('PROXY')}",
            "    type: select",
            "    proxies:",
            f"      - {_yaml_quote('AUTO')}",
        ])
        lines.extend(f"      - {_yaml_quote(name)}" for name in names)
    else:
        lines.extend([
            f"  - name: {_yaml_quote('PROXY')}",
            "    type: select",
            "    proxies:",
            f"      - {_yaml_quote('DIRECT')}",
        ])
    lines.extend([
        "rules:",
        "  - MATCH,PROXY",
        "",
    ])
    return "\n".join(lines)


def ladder_uri_lines(count, token, host, port, auth_version, platform):
    lines = []
    total = max(0, int(count))
    for index in range(1, total + 1):
        user, password = gateway_identity(index, auth_version, platform, token)
        tag = quote_plus(f"{platform}-{index:02d}")
        lines.append(
            f"http://{quote(user, safe='')}:{quote(password, safe='')}@{host}:{int(port)}#{tag}"
        )
    return lines


def ladder_base64(count, token, host, port, auth_version, platform):
    body = "\n".join(ladder_uri_lines(count, token, host, port, auth_version, platform))
    if body:
        body += "\n"
    return base64.b64encode(body.encode("utf-8")).decode("ascii")


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


def registration_proxy_url(config):
    """Resolve a server-only Resin proxy URL; never write it into manual settings."""
    token, auth_version = resin_auth(config)
    if not token:
        raise RuntimeError("未配置 Resin 代理认证，无法使用已生成节点")
    raw = (os.environ.get("NODES_RESIN_PROXY_URL") or config.get("resin_internal_proxy_url")
           or "http://127.0.0.1:8970")
    try:
        endpoint = urlsplit(str(raw).strip())
        valid = (endpoint.scheme in {"http", "https"} and endpoint.hostname and endpoint.port
                 and endpoint.username is None and endpoint.password is None
                 and endpoint.path in {"", "/"} and not endpoint.query and not endpoint.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise RuntimeError("Resin 内网代理地址无效，请检查服务端配置")
    platform = pool_settings(config)["gateway_platform"]
    # Keep one stable identity for Nodes, separate from exported n01/n02 identities.
    if auth_version.upper() in {"V1", "V1.0"}:
        user, password = f"{platform}.nodes-ops", token
    else:
        user, password = token, f"{platform}:nodes-ops"
    return f"{endpoint.scheme}://{quote(user, safe='')}:{quote(password, safe='')}@{endpoint.netloc}"
