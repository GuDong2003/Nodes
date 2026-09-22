"""Initialize private VPS settings once; never rotate existing credentials."""

import argparse
import json
import os
from pathlib import Path
import secrets

from werkzeug.security import generate_password_hash


def initialize(root, domain):
    directory = Path(root) / "data" / "config"
    names = ("config.local.json", "resin.env", "access.json")
    existing = [(directory / name).exists() for name in names]
    if all(existing):
        print("Existing deployment credentials preserved.")
        return
    if any(existing):
        raise RuntimeError("Partial existing configuration; refusing to overwrite secrets.")
    for name in ("config", "account", "node", "web", "resin/cache", "resin/state", "resin/log"):
        (Path(root) / "data" / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    password, admin_token, proxy_token = [secrets.token_urlsafe(32) for _ in range(3)]
    config = {
        "web_username": "admin",
        "web_password_hash": generate_password_hash(password),
        "web_session_secret": secrets.token_urlsafe(48),
        "export_token": secrets.token_urlsafe(32),
        "mail_provider": "yyds",
        "yyds_api_key": "",
        "captcha_provider": "browser",
        "captcha_api_key": "",
        "captcha_api_base": "https://api.2captcha.com",
        "captcha_timeout": 180,
        "captcha_poll_interval": 5,
        "pool_auto_register": False,
        "pool_expected_proxies_per_account": 100,
        "internal_base_url": "http://dashboard:8080/nodes",
        "resin_gateway_host": domain,
        "resin_gateway_port": 8970,
        "resin_gateway_platform": "Nodes",
        "resin_proxy_token": proxy_token,
        "resin_auth_version": "V1",
    }
    access = {
        "nodes_url": f"https://{domain}/nodes/",
        "nodes_username": "admin",
        "nodes_password": password,
        "resin_url": f"https://{domain}/ui/",
        "resin_admin_token": admin_token,
        "resin_proxy_token": proxy_token,
        "proxy_address": f"http://{domain}:8970",
        "proxy_username_example": "Nodes.n01",
    }
    contents = {
        "config.local.json": json.dumps(config, ensure_ascii=False, indent=2) + "\n",
        "access.json": json.dumps(access, ensure_ascii=False, indent=2) + "\n",
        "resin.env": f"RESIN_ADMIN_TOKEN={admin_token}\nRESIN_PROXY_TOKEN={proxy_token}\nRESIN_AUTH_VERSION=V1\n",
    }
    for name, body in contents.items():
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
    print("Private configuration initialized; credentials are in data/config/access.json (0600).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--domain", required=True)
    args = parser.parse_args()
    try:
        initialize(args.root, args.domain)
    except RuntimeError as error:
        parser.exit(1, str(error) + "\n")
