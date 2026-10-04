"""Proxy export format and Resin gateway output tests."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pool
import web_app


class ExportFormatTests(unittest.TestCase):
    def test_gateway_can_export_http_and_socks5_urls(self):
        http = pool.gpt_gateway_lines(
            2, "proxy-token", "ps.example.com", 8970, "V1", "Nodes"
        )
        socks5 = pool.socks5_gateway_lines(
            2, "proxy-token", "ps.example.com", 8970, "V1", "Nodes"
        )

        self.assertEqual(
            http[0], "http://Nodes.n01:proxy-token@ps.example.com:8970"
        )
        self.assertEqual(
            socks5[0], "socks5://Nodes.n01:proxy-token@ps.example.com:8970"
        )
        self.assertEqual(len(socks5), 2)

    def test_proxy_line_formats_preserve_or_remove_credentials(self):
        source = "http://user%40name:pass%3Aword@1.2.3.4:8080"

        self.assertEqual(pool.format_proxy_line(source, "http"), source)
        self.assertEqual(
            pool.format_proxy_line(source, "auth"),
            "user@name:pass:word@1.2.3.4:8080",
        )
        self.assertEqual(pool.format_proxy_line(source, "hostport"), "1.2.3.4:8080")

    def test_unknown_proxy_line_format_is_rejected(self):
        with self.assertRaises(ValueError):
            pool.format_proxy_line("http://user:pass@1.2.3.4:8080", "xml")


class DashboardExportFormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.node_dir = root / "node"
        self.node_dir.mkdir()
        self.config_file = root / "config.local.json"
        self.config_file.write_text(json.dumps({
            "export_token": "fixture-export",
            "resin_proxy_token": "fixture-proxy",
            "resin_gateway_host": "ps.example.com",
            "resin_gateway_port": 8970,
            "resin_gateway_platform": "Nodes",
            "resin_auth_version": "V1",
        }))
        self.patches = [
            patch.object(web_app, "NODE_DIR", self.node_dir),
            patch.object(web_app, "CONFIG_FILE", self.config_file),
            patch.object(web_app.app, "config", dict(web_app.app.config, TESTING=True)),
        ]
        for item in self.patches:
            item.start()
        self.client = web_app.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def authenticate(self):
        with self.client.session_transaction() as session:
            session.update(authenticated=True, username=web_app.WEB_USERNAME, csrf_token="csrf")

    def test_socks5_gateway_export_uses_resin_identity(self):
        settings = {
            "gateway_host": "ps.example.com",
            "gateway_port": 8970,
            "gateway_platform": "Nodes",
        }
        capacity = {"live_slots": 2}
        with patch.object(web_app, "_pool_snapshot", return_value=(settings, [], capacity)):
            response = self.client.get("/api/export/socks5?token=fixture-export")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_data(as_text=True).splitlines()[0],
            "socks5://Nodes.n01:fixture-proxy@ps.example.com:8970",
        )

    def test_proxy_file_download_can_render_auth_format(self):
        self.authenticate()
        (self.node_dir / "proxies_test.txt").write_text(
            "http://user%40name:pass%3Aword@1.2.3.4:8080\n"
        )
        response = self.client.get("/download/proxies/proxies_test.txt?format=auth")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_data(as_text=True),
            "user@name:pass:word@1.2.3.4:8080\n",
        )


if __name__ == "__main__":
    unittest.main()
