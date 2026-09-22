"""Rendered Nodes navigation must open the separate Resin page safely."""

from html.parser import HTMLParser
import unittest
from unittest.mock import patch

import web_app


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links.append(dict(attrs))


class PanelLinksTests(unittest.TestCase):
    def test_nodes_navigation_has_plain_new_tab_link_to_resin(self):
        with patch.dict(web_app.app.config, TESTING=True, SESSION_COOKIE_SECURE=False):
            client = web_app.app.test_client()
            with client.session_transaction() as session:
                session.update(authenticated=True, username=web_app.WEB_USERNAME)
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        parser = Links()
        parser.feed(response.get_data(as_text=True))
        links = [item for item in parser.links if item.get("id") == "open-resin"]
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link["href"], "https://ps.gudong226.com/ui/dashboard")
        self.assertEqual(link["target"], "_blank")
        self.assertEqual(set(link["rel"].split()), {"noopener", "noreferrer"})
        self.assertNotIn("data-view", link)


if __name__ == "__main__":
    unittest.main()
