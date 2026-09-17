import unittest
from unittest.mock import Mock, patch

import proxyscrape_register as register


class MailProviderTests(unittest.TestCase):
    def test_yunxin_create_mailbox(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "address": "ps123@mail.com",
            "domain": "mail.com",
            "kind": "mail",
        }
        with patch.object(register, "MAIL_API_KEY", "qm_test"), \
             patch.object(register, "MAIL_API_BASE", "https://mail.example"), \
             patch.object(register, "MAIL_TYPE", "mail"), \
             patch.object(register, "MAIL_SUFFIX", "mail.com"), \
             patch.object(register, "MAIL_DOMAIN", ""), \
             patch.object(register.requests, "post", return_value=response) as post:
            address, token = register.yunxin_create_mailbox()

        self.assertEqual(address, "ps123@mail.com")
        self.assertIsNone(token)
        self.assertEqual(post.call_args.kwargs["headers"]["X-API-Key"], "qm_test")
        self.assertEqual(post.call_args.kwargs["json"]["suffix"], "mail.com")

    def test_yunxin_wait_code_uses_codes_field(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "results": [{"subject": "Verify", "codes": ["a1b2c3d4e5"]}],
        }
        with patch.object(register, "MAIL_API_KEY", "qm_test"), \
             patch.object(register, "MAIL_API_BASE", "https://mail.example"), \
             patch.object(register.requests, "get", return_value=response):
            code = register.yunxin_wait_code("ps123@mail.com", timeout=1, interval=0)

        self.assertEqual(code, "a1b2c3d4e5")

    def test_yunxin_cf_mailbox_does_not_send_suffix(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"ok": True, "address": "ps123@mail.example"}
        with patch.object(register, "MAIL_API_KEY", "qm_test"), \
             patch.object(register, "MAIL_API_BASE", "https://mail.example"), \
             patch.object(register, "MAIL_TYPE", "cf"), \
             patch.object(register, "MAIL_SUFFIX", "mail.com"), \
             patch.object(register, "MAIL_DOMAIN", "mail.example"), \
             patch.object(register.requests, "post", return_value=response) as post:
            register.yunxin_create_mailbox()

        payload = post.call_args.kwargs["json"]
        self.assertNotIn("suffix", payload)
        self.assertEqual(payload["domain"], "mail.example")

    def test_yunxin_wait_code_parses_message_text(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "ok": True,
            "results": [{"subject": "ProxyScrape", "text": "Your verification code: z9y8x7w6"}],
        }
        with patch.object(register, "MAIL_API_KEY", "qm_test"), \
             patch.object(register, "MAIL_API_BASE", "https://mail.example"), \
             patch.object(register.requests, "get", return_value=response):
            code = register.yunxin_wait_code("ps123@mail.com", timeout=1, interval=0)

        self.assertEqual(code, "z9y8x7w6")

    def test_provider_dispatch_keeps_yyds_compatibility(self):
        with patch.object(register, "MAIL_PROVIDER", "yyds"), \
             patch.object(register, "yyds_create_mailbox", return_value=("a@example.com", "token")) as create:
            self.assertEqual(register.create_mailbox(), ("a@example.com", "token"))
            create.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
