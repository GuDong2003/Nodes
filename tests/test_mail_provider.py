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

    def test_cfmail_create_address_returns_address_and_jwt(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "address": "abc123@gudong226.com",
            "jwt": "address-jwt",
        }
        with patch.object(register, "random_mailbox_local", return_value="abc123"), \
             patch.object(register, "MAIL_API_BASE", "https://temp.example"), \
             patch.object(register, "MAIL_DOMAIN", "gudong226.com"), \
             patch.object(register.requests, "post", return_value=response) as post:
            address, token = register.cfmail_create_mailbox()

        self.assertEqual((address, token), ("abc123@gudong226.com", "address-jwt"))
        self.assertEqual(post.call_args.args[0], "https://temp.example/api/new_address")
        self.assertEqual(post.call_args.kwargs["json"], {
            "name": address.split("@", 1)[0],
            "domain": "gudong226.com",
            "cf_token": "",
            "enableRandomSubdomain": False,
            "enablePrefix": False,
        })

    def test_cfmail_wait_code_uses_address_jwt(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "results": [{"subject": "ProxyScrape", "text": "Your verification code: z9y8x7w6"}],
            "count": 1,
        }
        with patch.object(register, "MAIL_API_BASE", "https://temp.example"), \
             patch.object(register.requests, "get", return_value=response) as get:
            code = register.cfmail_wait_code(
                "abc123@gudong226.com", "address-jwt", timeout=1, interval=0,
            )

        self.assertEqual(code, "z9y8x7w6")
        self.assertEqual(get.call_args.args[0], "https://temp.example/api/mails")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer address-jwt")
        self.assertEqual(get.call_args.kwargs["params"], {"limit": 20, "offset": 0})

    def test_provider_dispatch_keeps_yyds_compatibility(self):
        with patch.object(register, "MAIL_PROVIDER", "yyds"), \
             patch.object(register, "yyds_create_mailbox", return_value=("a@example.com", "token")) as create:
            self.assertEqual(register.create_mailbox(), ("a@example.com", "token"))
            create.assert_called_once_with()

    def test_provider_dispatch_supports_cfmail(self):
        with patch.object(register, "MAIL_PROVIDER", "cfmail"), \
             patch.object(register, "cfmail_create_mailbox", return_value=("a@example.com", "jwt")) as create:
            self.assertEqual(register.create_mailbox(), ("a@example.com", "jwt"))
            create.assert_called_once_with()

    def test_wait_code_dispatch_passes_cfmail_jwt(self):
        with patch.object(register, "MAIL_PROVIDER", "cfmail"), \
             patch.object(register, "cfmail_wait_code", return_value="code") as wait:
            result = register.wait_mail_code(
                "a@example.com", timeout=12, interval=6, address_token="jwt",
            )

        self.assertEqual(result, "code")
        wait.assert_called_once_with("a@example.com", "jwt", timeout=12, interval=6)


if __name__ == "__main__":
    unittest.main()
