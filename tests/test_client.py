"""Synthetic contract tests. No real account, cookies, or network calls."""

from __future__ import annotations

import importlib
import json
import sys
import unittest
from datetime import date
from pathlib import Path
from types import ModuleType

# Load the independent client without importing HA's integration entry point.
package = ModuleType("tauron_ebok")
package.__path__ = [str(Path(__file__).parents[1] / "custom_components" / "tauron_ebok")]
sys.modules["tauron_ebok"] = package
client_module = importlib.import_module("tauron_ebok.client")
models = importlib.import_module("tauron_ebok.models")
portal = importlib.import_module("tauron_ebok.portal")
TauronClient = client_module.TauronClient
Response = client_module.Response
PortalChanged = models.PortalChanged
AuthenticationError = models.AuthenticationError
TODAY = date(2026, 10, 17)
PORTAL = portal.PORTAL
ACCOUNT = models.Account("TEST", "synthetic-client", "synthetic-checksum")
ARCHIVE_HTML = (
    '<div data-system="TEST" data-client-number="synthetic-client" '
    'data-client-number-check-sum="synthetic-checksum"></div>'
)
LOGIN_HTML = (
    '<form id="kc-form-login" method="post" '
    'action="https://logowanie.tauron.pl/realms/test/login-actions/authenticate?session_code=synthetic">'
    '<input type="hidden" name="execution" value="synthetic-hidden"></form>'
)


def document(index=1, **changes):
    return {
        "CustomerNumber": ACCOUNT.customer,
        "DocNumber": str(index),
        "DocSignature": f"TEST/{index}",
        "DocType": "FV",
        "SodId": f"synthetic-{index}",
        "AmountGross": "12.34",
        "IssueDate": "2026-10-01T00:00:00",
        "PaymentDate": "2026-10-15T00:00:00",
        "Status": "Opłacona",
        "IsDocumentCorrection": False,
        **changes,
    }


def page(rows, total=None, first=1, **changes):
    return Response(
        200,
        portal.DOCUMENTS,
        json.dumps(
            {
                "Status": "OK",
                "StatusEnum": 0,
                "Documents": rows,
                "RangeFrom": first,
                "RangeTo": first + 4,
                "TotalNumber": len(rows) if total is None else total,
                **changes,
            }
        ),
    )


def archive():
    return Response(200, portal.ARCHIVE, ARCHIVE_HTML)


class FakeTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.cleared = 0

    async def request(self, method, url, *, params=None, data=None):
        self.calls.append((method, url, params, data))
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def clear_cookies(self):
        self.cleared += 1


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def fetch(self, transport):
        return await TauronClient(transport, "synthetic-user", "synthetic-password").fetch(TODAY)

    async def test_configuration_error_identifies_archive_stage(self):
        transport = FakeTransport(Response(200, portal.ARCHIVE, "<html>unknown markup</html>"))
        with self.assertRaises(PortalChanged) as result:
            await self.fetch(transport)
        self.assertEqual(result.exception.ui_error, "portal_archive")

    async def test_configuration_error_identifies_login_stage(self):
        transport = FakeTransport(
            Response(401, portal.ARCHIVE, ""),
            Response(200, portal.LOGIN, "<html>unknown login</html>"),
        )
        with self.assertRaises(PortalChanged) as result:
            await self.fetch(transport)
        self.assertEqual(result.exception.ui_error, "portal_login")

    async def test_configuration_error_identifies_document_stage(self):
        transport = FakeTransport(archive(), Response(200, portal.DOCUMENTS, "{}"))
        with self.assertRaises(PortalChanged) as result:
            await self.fetch(transport)
        self.assertEqual(result.exception.ui_error, "portal_documents")

    async def test_existing_session_uses_get_only(self):
        transport = FakeTransport(archive(), page([document()]))
        result = await self.fetch(transport)
        self.assertEqual(len(result.documents), 1)
        self.assertEqual(result.latest.number, "TEST/1")
        self.assertTrue(all(call[0] == "GET" for call in transport.calls))

    async def test_login_posts_only_to_parsed_form_and_preserves_hidden_fields(self):
        transport = FakeTransport(
            Response(200, portal.LOGIN, LOGIN_HTML),
            Response(200, portal.LOGIN, LOGIN_HTML),
            Response(200, PORTAL, "<html>logged in</html>"),
            archive(),
            page([]),
        )
        result = await self.fetch(transport)
        posts = [call for call in transport.calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(
            posts[0][3],
            {
                "username": "synthetic-user",
                "password": "synthetic-password",
                "execution": "synthetic-hidden",
            },
        )
        self.assertEqual(transport.cleared, 1)
        self.assertEqual(result.documents, ())

    async def test_session_expiry_mid_scan_restarts_entire_scan(self):
        transport = FakeTransport(
            archive(),
            Response(401, portal.DOCUMENTS, ""),
            Response(200, portal.LOGIN, LOGIN_HTML),
            Response(200, PORTAL, ""),
            archive(),
            page([document(2)]),
        )
        result = await self.fetch(transport)
        self.assertEqual(result.latest.number, "TEST/2")
        self.assertEqual(len(transport.calls), 6)

    async def test_bad_credentials_do_not_loop(self):
        transport = FakeTransport(
            Response(401, portal.ARCHIVE, ""),
            Response(200, portal.LOGIN, LOGIN_HTML),
            Response(200, portal.LOGIN, LOGIN_HTML),
        )
        with self.assertRaises(AuthenticationError):
            await self.fetch(transport)
        self.assertEqual(len(transport.calls), 3)

    async def test_repeated_session_loss_does_not_loop(self):
        transport = FakeTransport(
            Response(401, portal.ARCHIVE, ""),
            Response(200, portal.LOGIN, LOGIN_HTML),
            Response(200, PORTAL, ""),
            Response(401, portal.ARCHIVE, ""),
        )
        with self.assertRaises(AuthenticationError):
            await self.fetch(transport)
        self.assertEqual(transport.cleared, 1)

    async def test_mfa_does_not_submit_credentials(self):
        transport = FakeTransport(Response(200, portal.LOGIN, '<input name="otp">'))
        with self.assertRaises(models.InteractiveLoginRequired):
            await self.fetch(transport)
        self.assertEqual(len(transport.calls), 1)

    async def test_all_pages_and_date_filter(self):
        transport = FakeTransport(
            archive(), page([document(i) for i in range(5)], 6), page([document(5)], 6, first=6)
        )
        result = await self.fetch(transport)
        self.assertEqual(len(result.documents), 6)
        query = transport.calls[-1][2]
        self.assertEqual((query["rangeFrom"], query["rangeTo"]), (6, 10))
        self.assertEqual(query["docKind"], "All")
        self.assertEqual(query["to"], "17.10.2026")
        self.assertEqual(result.date_from, date(2026, 4, 20))

    async def test_second_page_failure_never_returns_partial_snapshot(self):
        transport = FakeTransport(
            archive(),
            page([document(i) for i in range(5)], 6),
            models.ConnectionError("Synthetic failure"),
        )
        with self.assertRaises(models.ConnectionError):
            await self.fetch(transport)

    async def test_duplicate_pages_rejected(self):
        transport = FakeTransport(
            archive(), page([document(i) for i in range(5)], 6), page([document(0)], 6, first=6)
        )
        with self.assertRaises(PortalChanged):
            await self.fetch(transport)

    async def test_total_change_rejected(self):
        transport = FakeTransport(
            archive(), page([document(i) for i in range(5)], 6), page([document(5)], 7, first=6)
        )
        with self.assertRaises(PortalChanged):
            await self.fetch(transport)

    async def test_incomplete_page_rejected(self):
        with self.assertRaises(PortalChanged):
            await self.fetch(FakeTransport(archive(), page([document()], 6)))

    async def test_range_mismatch_rejected(self):
        with self.assertRaises(PortalChanged):
            await self.fetch(FakeTransport(archive(), page([document()], first=6)))

    async def test_wrong_owner_rejected(self):
        with self.assertRaises(PortalChanged):
            await self.fetch(FakeTransport(archive(), page([document(CustomerNumber="other")])))

    async def test_malformed_payloads_are_not_empty_success(self):
        for body in ("<html>error</html>", "{}", "[]", '{"Status":"ERROR"}'):
            with self.subTest(body=body), self.assertRaises(PortalChanged):
                await self.fetch(FakeTransport(archive(), Response(200, portal.DOCUMENTS, body)))

    async def test_unrecognized_archive_fails_without_sending_account_queries(self):
        transport = FakeTransport(Response(200, portal.ARCHIVE, "<html>changed markup</html>"))
        with self.assertRaises(PortalChanged):
            await self.fetch(transport)
        self.assertEqual(len(transport.calls), 1)

    async def test_two_accounts_do_not_get_mixed(self):
        html = ARCHIVE_HTML + ARCHIVE_HTML.replace("synthetic-client", "second-client")
        transport = FakeTransport(
            Response(200, portal.ARCHIVE, html),
            page([document()]),
            page([document(CustomerNumber="second-client")]),
        )
        result = await self.fetch(transport)
        self.assertEqual(result.account_count, 2)
        self.assertEqual(len(result.documents), 2)
        self.assertNotEqual(result.documents[0].key, result.documents[1].key)


class ParserTests(unittest.TestCase):
    def test_key_is_stable_when_payment_status_changes(self):
        first = models.Invoice.parse(ACCOUNT, document())
        second = models.Invoice.parse(ACCOUNT, document(Status="Inny status"))
        self.assertEqual(first.key, second.key)
        self.assertNotEqual(first.status, second.status)

    def test_unknown_status_and_correction_are_preserved(self):
        invoice = models.Invoice.parse(
            ACCOUNT, document(Status="Nieznany", IsDocumentCorrection=None)
        )
        self.assertEqual(invoice.status, "Nieznany")
        self.assertIsNone(invoice.correction)

    def test_correction_flag(self):
        self.assertTrue(
            models.Invoice.parse(ACCOUNT, document(IsDocumentCorrection=True)).correction
        )

    def test_missing_fields_stay_unknown(self):
        invoice = models.Invoice.parse(
            ACCOUNT, document(AmountGross=None, PaymentDate=None, IssueDate=None)
        )
        self.assertIsNone(invoice.gross)
        self.assertIsNone(invoice.due)
        self.assertIsNone(invoice.issued)

    def test_invalid_values_fail_closed(self):
        for field, value in [
            ("AmountGross", "NaN"),
            ("AmountGross", True),
            ("IssueDate", "bad-date"),
            ("IsDocumentCorrection", "false"),
            ("DocNumber", None),
            ("Status", {}),
        ]:
            with self.subTest(field=field, value=value), self.assertRaises(PortalChanged):
                models.Invoice.parse(ACCOUNT, document(**{field: value}))

    def test_secrets_are_not_in_object_repr(self):
        self.assertNotIn(ACCOUNT.customer, repr(ACCOUNT))
        self.assertNotIn("synthetic-secret", repr(Response(200, PORTAL, "synthetic-secret")))
        self.assertNotIn("TEST/1", repr(models.Invoice.parse(ACCOUNT, document())))

    def test_discovery_from_json(self):
        html = (
            '<script type="application/json">'
            + json.dumps(
                {
                    "accounts": [
                        {
                            "clientNumber": ACCOUNT.customer,
                            "clientNumberCheckSum": ACCOUNT.checksum,
                            "system": ACCOUNT.system,
                        }
                    ]
                }
            )
            + "</script>"
        )
        self.assertEqual(portal.discover_accounts(html), (ACCOUNT,))

    def test_discovery_from_query(self):
        html = (
            '<a href="/api/test?clientNumber=synthetic-client&amp;'
            'clientNumberCheckSum=synthetic-checksum&amp;system=TEST">x</a>'
        )
        self.assertEqual(portal.discover_accounts(html), (ACCOUNT,))

    def test_incomplete_account_values_never_paired(self):
        with self.assertRaises(PortalChanged):
            portal.discover_accounts(
                '<div data-client-number="one"></div>'
                '<div data-client-number-check-sum="two" data-system="TEST"></div>'
            )

    def test_conflicting_contexts_rejected(self):
        with self.assertRaises(PortalChanged):
            portal.discover_accounts(
                ARCHIVE_HTML + ARCHIVE_HTML.replace("synthetic-checksum", "other-checksum")
            )

    def test_login_action_is_html_decoded(self):
        form = portal.login_form(
            LOGIN_HTML.replace("session_code=synthetic", "a=1&amp;b=2"), portal.LOGIN
        )
        self.assertIn("?a=1&b=2", form.action)

    def test_credential_destination_is_restricted(self):
        for target in (
            "https://example.com/login",
            "http://logowanie.tauron.pl/login",
            "https://moj.tauron.pl/login",
            "https://logowanie.tauron.pl@evil.test/login",
        ):
            with self.subTest(target=target), self.assertRaises(PortalChanged):
                portal.login_form(
                    LOGIN_HTML.replace(
                        "https://logowanie.tauron.pl/realms/test/login-actions/authenticate?session_code=synthetic",
                        target,
                    ),
                    portal.LOGIN,
                )

    def test_captcha_is_not_bypassed(self):
        with self.assertRaises(models.InteractiveLoginRequired):
            portal.login_form('<div class="g-recaptcha"></div>' + LOGIN_HTML, portal.LOGIN)


if __name__ == "__main__":
    unittest.main()
