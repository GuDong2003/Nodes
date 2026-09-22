"""Exercise bootstrap against a local fake of Resin's documented API boundary."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest


class ResinBootstrapTests(unittest.TestCase):
    def test_bootstrap_wires_nodes_and_keeps_public_endpoint_proxy_only(self):
        objects = {"subscriptions": [], "platforms": [], "endpoints": []}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, status, data):
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode())

            def do_GET(self):
                if self.headers.get("Authorization") != "Bearer fixture-admin":
                    return self.respond(401, {})
                kind = self.path.split("?")[0].rsplit("/", 1)[1]
                self.respond(200, {"items": objects[kind], "total": len(objects[kind])})

            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer fixture-admin":
                    return self.respond(401, {})
                kind = self.path.rsplit("/", 1)[1]
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                body["id"] = f"{kind}-{len(objects[kind])}"
                objects[kind].append(body)
                self.respond(201, body)

            def do_PATCH(self):
                if self.headers.get("Authorization") != "Bearer fixture-admin":
                    return self.respond(401, {})
                kind, identifier = self.path.split("/")[-2:]
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                item = next(item for item in objects[kind] if item["id"] == identifier)
                item.update(body)
                self.respond(200, item)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "access.json").write_text(json.dumps({"resin_admin_token": "fixture-admin"}))
                (root / "config.local.json").write_text(json.dumps({
                    "export_token": "fixture-export",
                    "internal_base_url": "http://dashboard:8080/nodes",
                }))
                command = [sys.executable, str(Path(__file__).parent / "deploy/configure_resin.py"),
                           "--config-dir", directory,
                           "--resin-base", f"http://127.0.0.1:{server.server_port}"]
                for _ in range(2):
                    result = subprocess.run(command, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertNotIn("fixture-admin", result.stdout)
                    self.assertNotIn("fixture-export", result.stdout)
                self.assertEqual([len(objects[k]) for k in objects], [1, 1, 1])
                endpoint = objects["endpoints"][0]
                self.assertEqual(endpoint["port"], 8970)
                self.assertFalse(endpoint["allow_management"])
                self.assertTrue(endpoint["require_proxy_auth_info"])
                self.assertFalse(endpoint["allow_http_reverse"])
                self.assertEqual(objects["subscriptions"][0]["url"],
                                 "http://dashboard:8080/nodes/api/export/live-proxies?token=fixture-export")
                self.assertEqual(objects["platforms"][0]["regex_filters"], ["^Nodes/"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
