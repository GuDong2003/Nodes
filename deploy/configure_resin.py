"""Connect Resin to Nodes without registering accounts or printing credentials."""

import argparse
import json
from pathlib import Path

import requests


def configure(config_dir, resin_base):
    config = json.loads((config_dir / "config.local.json").read_text())
    access = json.loads((config_dir / "access.json").read_text())
    session = requests.Session()
    session.trust_env = False
    session.headers["Authorization"] = "Bearer " + access["resin_admin_token"]

    def call(method, path, body=None):
        response = session.request(method, resin_base.rstrip("/") + "/api/v1/" + path,
                                   json=body, timeout=30)
        if not response.ok:
            raise RuntimeError(f"Resin {method} {path.split('?')[0]} returned {response.status_code}")
        return response.json()

    def ensure(kind, key, wanted, body, patch_body=None):
        items = call("GET", kind + "?limit=1000")["items"]
        existing = next((item for item in items if item.get(key) == wanted), None)
        if existing:
            result = call("PATCH", kind + "/" + existing["id"],
                          body if patch_body is None else patch_body)
        else:
            result = call("POST", kind, body)
        print(f"Configured {kind}: {result['id']}")
        return result

    ensure("platforms", "name", "Nodes", {
        "name": "Nodes", "regex_filters": ["^Nodes/"], "sticky_ttl": "168h",
    })
    ensure("subscriptions", "name", "Nodes", {
        "name": "Nodes", "source_type": "remote", "enabled": True,
        "url": config["internal_base_url"].rstrip("/") + "/api/export/live-proxies?token=" + config["export_token"],
        "update_interval": "2m", "incremental_alive_nodes": False,
    }, {
        "name": "Nodes", "enabled": True,
        "url": config["internal_base_url"].rstrip("/") + "/api/export/live-proxies?token=" + config["export_token"],
        "update_interval": "2m", "incremental_alive_nodes": False,
    })
    ensure("endpoints", "port", 8970, {
        "port": 8970, "enabled": True, "allow_management": False,
        "allow_proxy": True, "require_proxy_auth_info": True,
        "allow_http_forward": True, "allow_http_reverse": False, "allow_socks5": True,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=Path("/app/config"))
    parser.add_argument("--resin-base", default="http://resin:2260")
    args = parser.parse_args()
    configure(args.config_dir, args.resin_base)
