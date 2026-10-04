"""Validated, versioned proxy quality profiles (adapted from upstream Nodes)."""

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit


DEFAULTS = {
    "proxy_quality_enabled": False,
    "proxy_max_latency_ms": 3000,
    "proxy_exclude_countries": "",
    "proxy_arp_check_enabled": False,
    "proxy_arp_probe_url": "https://cp.cloudflare.com/generate_204",
    "proxy_quality_workers": 8,
    "proxy_quality_cache_ttl_sec": 600,
    "proxy_quality_timeout_sec": 12,
}
COUNTRY_CODES = frozenset("""
AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN
BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL
GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM
JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME
MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP
NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD
SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO
TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW
""".split())


def profile_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError("规则 ID 只能使用小写字母、数字、下划线或连字符（最多 64 位）")
    return value


def validate_fields(data, base=None):
    if not isinstance(data, dict):
        raise ValueError("规则必须是 JSON 对象")
    if set(data) - (set(DEFAULTS) | {"name"}):
        raise ValueError("包含未知规则字段")
    values = {**DEFAULTS, **{k: v for k, v in (base or {}).items() if k in DEFAULTS}, **data}
    for key in ("proxy_quality_enabled", "proxy_arp_check_enabled"):
        if not isinstance(values[key], bool):
            raise ValueError(f"{key} 必须是布尔值")
    bounds = {
        "proxy_max_latency_ms": (1, 120000), "proxy_quality_workers": (1, 32),
        "proxy_quality_cache_ttl_sec": (10, 86400), "proxy_quality_timeout_sec": (1, 60),
    }
    for key, (low, high) in bounds.items():
        value = values[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError(f"{key} 必须是 {low}-{high} 之间的整数")
    countries = values["proxy_exclude_countries"]
    if not isinstance(countries, str):
        raise ValueError("排除国家请填写逗号分隔的两位国家代码")
    codes = [code.strip().upper() for code in countries.split(",") if code.strip()]
    if any(code not in COUNTRY_CODES for code in codes):
        raise ValueError("排除国家必须使用两位国家代码，例如 JP,SG")
    values["proxy_exclude_countries"] = ",".join(dict.fromkeys(codes))
    url = values["proxy_arp_probe_url"]
    try:
        if not isinstance(url, str) or any(ch.isspace() for ch in url):
            raise ValueError()
        parsed = urlsplit(url)
        port = parsed.port
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.fragment or (port is not None and port == 0)):
            raise ValueError()
    except ValueError as error:
        raise ValueError("目标探测地址必须是有效的 HTTPS URL，且不能包含账号密码或片段") from error
    if "name" in values:
        if not isinstance(values["name"], str) or not 1 <= len(values["name"].strip()) <= 80:
            raise ValueError("规则名称须为 1-80 个字符")
        values["name"] = values["name"].strip()
    return values


def profiles_state(config):
    """Read without persisting a synthetic default or exposing unrelated config."""
    raw = config.get("quality_profiles")
    profiles = {}
    for pid, row in (raw.items() if isinstance(raw, dict) else ()):
        if not isinstance(row, dict):
            continue
        profile_id(pid)
        fields = validate_fields({k: row[k] for k in DEFAULTS if k in row})
        profiles[pid] = {**fields, "id": pid, "name": str(row.get("name") or pid)[:80],
                         "version": max(0, int(row.get("version") or 0)),
                         "updated_at": str(row.get("updated_at") or "")}
    if "default" not in profiles:
        fields = validate_fields({k: config[k] for k in DEFAULTS if k in config})
        profiles["default"] = {**fields, "id": "default", "name": "默认规则", "version": 0, "updated_at": ""}
    active = config.get("quality_profile_id") or "default"
    active = active if active in profiles else "default"
    export = config.get("export_quality_profile") or active
    export = export if export in profiles else active
    return {"active_id": active, "export_id": export,
            "profiles": [profiles[pid] for pid in sorted(profiles)]}


def resolve_profile(config, requested=None, for_export=False):
    state = profiles_state(config)
    wanted = profile_id(requested) if requested is not None else state["export_id" if for_export else "active_id"]
    for row in state["profiles"]:
        if row["id"] == wanted:
            return row
    raise ValueError("未找到指定的质检规则")


def save_profile(config, pid, payload):
    pid = profile_id(pid)
    if not isinstance(payload, dict) or not payload:
        raise ValueError("请提供规则字段")
    state = profiles_state(config)
    profiles = {row["id"]: row for row in state["profiles"]}
    if pid not in profiles and len(profiles) >= 32:
        raise ValueError("最多保存 32 条规则")
    previous = profiles.get(pid, {})
    fields = validate_fields(payload, previous)
    row = {**fields, "id": pid, "name": fields.get("name") or previous.get("name") or pid,
           "version": previous.get("version", 0) + 1,
           "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    profiles[pid] = row
    updated = {**config, "quality_profiles": profiles}
    if pid == state["active_id"]:
        updated.update({key: row[key] for key in DEFAULTS})
    return updated, row


def activate_profile(config, pid):
    row = resolve_profile(config, pid)
    return {**config, "quality_profile_id": row["id"], "export_quality_profile": row["id"],
            **{key: row[key] for key in DEFAULTS}}, row
