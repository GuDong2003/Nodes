"""TLS/login/storage/bootstrap smoke test; never starts a registration task."""

import argparse
import json
from pathlib import Path
import re
from urllib.parse import quote, urlsplit

import requests


def verify(config_dir):
    access = json.loads((config_dir / "access.json").read_text())
    before = json.loads((config_dir / "config.local.json").read_text())
    nodes_url = access["nodes_url"].rstrip("/")
    public_base = nodes_url.removesuffix("/nodes")
    client = requests.Session()
    client.trust_env = False

    def get(path, status=200, **kwargs):
        response = client.get(public_base + path, timeout=15, **kwargs)
        assert response.status_code == status, f"GET {path}: {response.status_code}"
        return response

    get("/nodes/api/health")
    for path in ("/nodes/api/dashboard", "/nodes/api/settings", "/api/v1/system/info",
                 "/nodes/api/export/live-proxies", "/nodes/api/export/clash.yml",
                 "/nodes/api/export/gpt-gateway", "/nodes/api/export/ladder",
                 "/nodes/api/export/socks5"):
        get(path, 401)
    print("HTTPS verified; unauthenticated management and exports denied.")

    login = client.post(nodes_url + "/login", data={"username": access["nodes_username"],
                        "password": access["nodes_password"]}, allow_redirects=False, timeout=15)
    assert login.status_code == 302, f"login: {login.status_code}"
    cookie = login.headers.get("Set-Cookie", "")
    assert all(value in cookie for value in ("Secure", "HttpOnly", "SameSite=Strict"))
    page = get("/nodes/")
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
    dashboard = get("/nodes/api/dashboard").json()
    assert not dashboard["active_task"], "Unexpected registration task"
    assert not dashboard["pool"]["auto_register"]
    assert dashboard["pool"]["has_resin_token"]
    for key in ("subscription_url_public", "gpt_subscription_url", "clash_subscription_url",
                "ladder_subscription_url", "socks5_subscription_url"):
        assert dashboard["pool"][key].startswith(nodes_url + "/api/export/")
    print("Login/cookie/CSRF token and HTTPS subscription URLs verified.")

    # An invalid count is used as an extra guard: even broken CSRF cannot register.
    denied = client.post(nodes_url + "/api/tasks", json={"count": 0}, timeout=15)
    assert denied.status_code == 403, f"Missing CSRF returned {denied.status_code}"
    saved = client.put(nodes_url + "/api/settings", json={"captcha_timeout": before["captcha_timeout"]},
                       headers={"X-CSRF-Token": csrf}, timeout=15)
    assert saved.status_code == 200, f"Config save: {saved.status_code}"
    after = json.loads((config_dir / "config.local.json").read_text())
    for key in ("export_token", "web_password_hash", "web_session_secret", "resin_proxy_token"):
        assert before[key] == after[key], "Config update changed a secret"
    assert (config_dir / "config.local.json").stat().st_mode & 0o777 == 0o600
    print("CSRF rejection and private directory-mounted config persistence verified.")

    headers = {"Authorization": "Bearer " + access["resin_admin_token"]}
    get("/ui/")
    get("/api/v1/system/info", headers=headers)
    platforms = get("/api/v1/platforms", headers=headers).json()["items"]
    assert any(item["name"] == "Nodes" for item in platforms)
    endpoints = get("/api/v1/endpoints", headers=headers).json()["items"]
    endpoint = next(item for item in endpoints if item["port"] == 8970)
    assert not endpoint["allow_management"] and endpoint["require_proxy_auth_info"]
    assert endpoint["status"] == "active", f"Endpoint state: {endpoint['status']}"
    subscriptions = get("/api/v1/subscriptions", headers=headers).json()["items"]
    subscription = next(item for item in subscriptions if item["name"] == "Nodes")
    assert subscription["url"].startswith("http://dashboard:8080/nodes/")
    exported = client.get("http://dashboard:8080/nodes/api/export/live-proxies",
                          headers={"X-Export-Token": before["export_token"]}, timeout=10)
    assert exported.status_code == 200
    print(f"Resin admin/platform/subscription linked; {subscription['node_count']} upstream nodes.")

    proxy = access["proxy_address"]
    denied = client.get("http://example.invalid/", proxies={"http": proxy}, timeout=10)
    assert denied.status_code == 407, f"Proxy without credentials: {denied.status_code}"
    blocked = client.get(proxy + "/api/v1/system/info", timeout=10)
    assert blocked.status_code in (403, 404), f"Public management: {blocked.status_code}"
    print("Public proxy requires credentials and does not expose management.")
    if dashboard["pool"]["live_slots"] == 0:
        address = urlsplit(proxy)
        authenticated_proxy = (f"{address.scheme}://Nodes.n01:"
                               f"{quote(access['resin_proxy_token'], safe='')}@{address.netloc}")
        empty = client.get("http://example.invalid/", proxies={"http": authenticated_proxy}, timeout=10)
        assert empty.status_code == 503, f"Authenticated empty proxy pool: {empty.status_code}"
        print("Valid proxy credentials accepted; empty pool correctly returns 503.")
    print(f"Accounts={dashboard['summary']['accounts']}, live_slots={dashboard['pool']['live_slots']}; no registration performed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("/app/config"))
    verify(parser.parse_args().config_dir)
