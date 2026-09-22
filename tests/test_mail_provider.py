import base64
import contextlib
import io
import json
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


class CfmailMimeTests(unittest.TestCase):
    # Mirrors CF's raw-mail row shape, with invented addresses and codes only.
    RAW_MULTIPART = (
        'From: ProxyScrape <noreply@proxyscrape.example>\r\n'
        'To: test@example.com\r\n'
        'Subject: ProxyScrape - Email Verification\r\n'
        'MIME-Version: 1.0\r\n'
        'Content-Type: multipart/alternative; boundary="fixture-boundary"\r\n'
        '\r\n'
        '--fixture-boundary\r\n'
        'Content-Type: text/plain; charset=utf-8\r\n'
        'Content-Transfer-Encoding: 7bit\r\n'
        '\r\n'
        'Please open the HTML version of this email.\r\n'
        '--fixture-boundary\r\n'
        'Content-Type: text/html; charset=utf-8\r\n'
        'Content-Transfer-Encoding: quoted-printable\r\n'
        '\r\n'
        '<p>Here is your email verifi=\r\n'
        'cation code:&nbsp;<strong>a1b2c3=\r\n'
        'd4e5</strong></p>\r\n'
        '--fixture-boundary--\r\n'
    )

    def poll(self, raw_messages):
        rows = [{
            "id": index, "address": "test@example.com", "source": "noreply@proxyscrape.example",
            "message_id": f"fixture-{index}", "raw": raw,
            "metadata": None, "created_at": "2026-01-01 00:00:00",
        } for index, raw in enumerate(raw_messages, 1)]
        response = register.requests.Response()
        response.status_code = 200
        response._content = json.dumps({"results": rows, "count": len(rows)}).encode()
        output = io.StringIO()
        with patch.object(register, "MAIL_PROVIDER", "cfmail"), \
             patch.object(register, "MAIL_API_BASE", "https://temp.example"), \
             patch.object(register.requests, "get", return_value=response) as get, \
             patch.object(register.time, "time", side_effect=[0, 0, 2]), \
             contextlib.redirect_stdout(output):
            try:
                code = register.wait_mail_code("test@example.com", timeout=1, interval=0,
                                               address_token="private-mailbox-jwt")
            except TimeoutError:
                code = None
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer private-mailbox-jwt")
        return code, output.getvalue()

    def test_poll_extracts_raw_quoted_printable_html(self):
        code, _ = self.poll([self.RAW_MULTIPART])
        self.assertEqual(code, "a1b2c3d4e5")

    def test_poll_decodes_raw_base64_text(self):
        raw = ("MIME-Version: 1.0\r\nContent-Type: text/plain; charset=utf-8\r\n"
               "Content-Transfer-Encoding: base64\r\n\r\n"
               "WW91ciB2ZXJpZmljYXRpb24gY29kZTogejl5OHg3dzYK\r\n")
        code, _ = self.poll([raw])
        self.assertEqual(code, "z9y8x7w6")

    def test_poll_decodes_declared_non_utf8_charset(self):
        body = base64.b64encode("验证码： k7m8n9p0".encode("gb18030")).decode("ascii")
        raw = ("Content-Type: text/plain; charset=gb18030\r\n"
               "Content-Transfer-Encoding: base64\r\n\r\n" + body)
        code, _ = self.poll([raw])
        self.assertEqual(code, "k7m8n9p0")

    def test_unknown_charset_does_not_abort_polling(self):
        raw = ("Content-Type: text/plain; charset=invalid-charset\r\n\r\n"
               "Your verification code: p0q1r2s3")
        code, _ = self.poll([raw])
        self.assertEqual(code, "p0q1r2s3")

    def test_unusable_row_does_not_hide_a_later_message(self):
        code, _ = self.poll([None, {"invalid": "raw type"}, "no verification message", self.RAW_MULTIPART])
        self.assertEqual(code, "a1b2c3d4e5")

    def test_broken_mime_does_not_hide_a_later_message(self):
        broken_boundary = ('Content-Type: multipart/alternative; boundary="missing"\r\n\r\n'
                           '--different\r\nContent-Type: text/html\r\n\r\nNo body.\r\n')
        broken_encoding = ('Content-Type: text/plain; charset=utf-8\r\n'
                           'Content-Transfer-Encoding: base64\r\n\r\n%%%A===\r\n')
        code, _ = self.poll([broken_boundary, broken_encoding, self.RAW_MULTIPART])
        self.assertEqual(code, "a1b2c3d4e5")

    def test_does_not_extract_header_or_attachment_codes(self):
        raw = (
            'Subject: verification code: decoy123\r\n'
            'Content-Type: multipart/mixed; boundary="outer"\r\n\r\n'
            '--outer\r\nContent-Type: text/plain\r\n\r\nNo code yet.\r\n'
            '--outer\r\nContent-Type: text/plain\r\n'
            'Content-Disposition: attachment; filename="secret.txt"\r\n\r\n'
            'verification code: wrong123\r\n'
            '--outer\r\nContent-Type: message/rfc822\r\n'
            'Content-Disposition: attachment; filename="forwarded.eml"\r\n\r\n'
            'Content-Type: text/plain\r\n\r\nverification code: wrong456\r\n'
            '--outer--\r\n'
        )
        code, _ = self.poll([raw])
        self.assertIsNone(code)

    def test_body_falls_back_to_text_when_html_has_no_code(self):
        raw = self.RAW_MULTIPART.replace(
            "Please open the HTML version of this email.", "verification code: t1u2v3w4"
        ).replace("a1b2c3=\r\nd4e5", "")
        code, _ = self.poll([raw])
        self.assertEqual(code, "t1u2v3w4")

    def test_raw_mail_and_code_are_not_logged(self):
        code, logs = self.poll([self.RAW_MULTIPART])
        self.assertEqual(code, "a1b2c3d4e5")
        for secret in ("a1b2c3d4e5", "private-mailbox-jwt", "test@example.com", "<strong>"):
            self.assertNotIn(secret, logs)


if __name__ == "__main__":
    unittest.main()
