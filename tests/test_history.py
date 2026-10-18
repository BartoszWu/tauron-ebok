"""Synthetic fixtures match the observed ByPoint schema, without personal data."""

from __future__ import annotations

import copy
import importlib
import json
import unittest
from datetime import date

from test_client import LOGIN_HTML, PORTAL, FakeTransport, Response, TauronClient

h = importlib.import_module("tauron_ebok.history")
models = importlib.import_module("tauron_ebok.models")
TARGET = h.HistoryTarget("TEST", "synthetic-billing", "synthetic-pod")
START, END = date(2026, 1, 1), date(2026, 10, 18)
HOME = (
    '<a href="/api/sitecore/Home/LogOut">Out</a>'
    '<a href="/Home/Pages/Faktury?sid=TEST&amp;pp=synthetic-billing">Invoices</a>'
)
LIST = (
    '<a href="/Home/Pages/historia-odczytow-licznika-prad/szczegoly?sid=TEST'
    '&amp;cid=synthetic-billing&amp;checksum=synthetic">Details</a>'
)
DETAIL = (
    '<div id="historia-odczytow" data-unit="kWh" data-append-params '
    'data-api-by-point="/api/sitecore/EnergyConsumption/ByPoint" '
    'data-billing-number="synthetic-billing"></div><input class="point-checkbox" '
    'data-point-id="synthetic-pod" data-point-address="Synthetic home">'
)


def reading(day="22.01.2026", value=100, kind=4):
    return {
        "LastReadingDate": day,
        "Type": kind,
        "Details": [
            {"Zone": "AggregatedZone", "Consumption": value, "ConsumptionIndication": 9000}
        ],
    }


def payload(*rows):
    return [
        {
            "Number": TARGET.pod,
            "Readings": list(rows or [reading()]),
            "MeterPointName": "MUST NOT BE STORED",
            "Address": {"FullAddress": "PRIVATE"},
        }
    ]


def responses(data=None):
    return [
        Response(200, PORTAL, HOME),
        Response(200, h.HISTORY_LIST, LIST),
        Response(200, PORTAL + h.DETAIL_PATH, DETAIL),
        Response(200, h.BY_POINT, json.dumps(data or payload())),
    ]


class HistoryParsingTests(unittest.TestCase):
    def parse(self, data):
        return h.parse_readings(data, TARGET, START, END)

    def test_multiple_readings_per_month_are_summed_without_calendar_estimates(self):
        result = self.parse(payload(reading("22.07.2026", 10), reading("27.07.2026", 3, 2)))
        self.assertEqual(
            h.monthly_rows(result),
            [{"month": "2026-07", "kwh": 13.0, "reading_dates": ["2026-07-22", "2026-07-27"]}],
        )
        self.assertNotIn("PRIVATE", repr([r.serialize() for r in result]))
        self.assertNotIn("9000", repr([r.serialize() for r in result]))

    def test_missing_months_are_not_invented_and_unknown_is_not_zero(self):
        rows = h.monthly_rows(self.parse(payload(reading(value=None), reading("22.03.2026", 0))))
        self.assertEqual([m["month"] for m in rows], ["2026-01", "2026-03"])
        self.assertIsNone(rows[0]["kwh"])
        self.assertEqual(rows[1]["kwh"], 0)

    def test_aggregated_zone_not_double_counted(self):
        row = reading()
        row["Details"].append({"Zone": "Peak", "Consumption": 100})
        self.assertEqual(h.monthly_rows(self.parse(payload(row)))[0]["kwh"], 100)

    def test_separate_zones_sum(self):
        row = reading()
        row["Details"] = [{"Zone": "One", "Consumption": 1.2}, {"Zone": "Two", "Consumption": 2.3}]
        self.assertEqual(h.monthly_rows(self.parse(payload(row)))[0]["kwh"], 3.5)

    def test_invalid_or_other_account_payloads_fail_closed(self):
        bad = [
            [],
            {},
            {"success": False},
            payload(reading(), reading()),
            payload(reading(value=True)),
            payload(reading(value=-1)),
            payload(reading(value="NaN")),
            payload(reading("bad")),
            payload(reading("01.01.2025")),
            payload(reading("31.12.2026")),
        ]
        other = payload()
        other[0]["Number"] = "other-pod"
        bad.append(other)
        empty = payload()
        empty[0]["Readings"] = []
        bad.append(empty)
        bad.append(payload() + copy.deepcopy(payload()))
        for data in bad:
            with self.subTest(data=str(data)[:50]), self.assertRaises(models.PortalChanged):
                self.parse(data)

    def test_duplicate_zones_fail(self):
        row = reading()
        row["Details"] *= 2
        with self.assertRaises(models.PortalChanged):
            self.parse(payload(row))

    def test_discover_active_accounts_and_strict_details(self):
        self.assertEqual(len(h.history_html(HOME).accounts), 1)
        self.assertEqual(len(h.history_html(LIST).details), 1)
        page = h.history_html(DETAIL)
        self.assertEqual(page.points, {TARGET.pod: "Synthetic home"})
        self.assertEqual(page.endpoint, h.BY_POINT)
        self.assertNotIn("synthetic-billing", repr(TARGET))


class HistoryClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_selected_point_gets_history_no_invoices_or_pdf(self):
        transport = FakeTransport(*responses())
        result = await TauronClient(transport, "user", "pass").fetch_history(TARGET, START, END)
        self.assertEqual(len(result), 1)
        self.assertEqual([c[0] for c in transport.calls], ["GET"] * 4)
        self.assertEqual(
            transport.calls[-1][2],
            {
                "billingNumber": TARGET.billing,
                "from": "2026-01-01",
                "to": "2026-10-18",
                "Pods": TARGET.pod,
            },
        )

    async def test_expired_session_relogs_once_then_retries_full_read(self):
        transport = FakeTransport(
            Response(200, PORTAL, LOGIN_HTML),
            Response(200, PORTAL, LOGIN_HTML),
            Response(200, PORTAL, HOME),
            *responses(),
        )
        await TauronClient(transport, "user", "pass").fetch_history(TARGET, START, END)
        self.assertEqual(sum(c[0] == "POST" for c in transport.calls), 1)
        self.assertEqual(transport.cleared, 1)

    async def test_mismatched_point_or_billing_stops_before_consumption_request(self):
        for html in [
            DETAIL.replace("synthetic-pod", "other-pod"),
            DETAIL.replace("synthetic-billing", "other-billing"),
            DETAIL.replace("/ByPoint", "/Other"),
        ]:
            replies = responses()
            replies[2] = Response(200, PORTAL + h.DETAIL_PATH, html)
            transport = FakeTransport(*replies)
            with self.assertRaises(h.HistoryChanged):
                await TauronClient(transport, "u", "p").fetch_history(TARGET, START, END)
            self.assertFalse(any(c[1] == h.BY_POINT for c in transport.calls))

    async def test_inactive_account_is_not_queried(self):
        transport = FakeTransport(Response(200, PORTAL, HOME.replace("synthetic-billing", "other")))
        with self.assertRaises(h.HistoryChanged):
            await TauronClient(transport, "u", "p").fetch_history(TARGET, START, END)
        self.assertEqual(len(transport.calls), 1)

    async def test_bad_json_is_not_empty_history(self):
        replies = responses()
        replies[-1] = Response(200, h.BY_POINT, "<html>error</html>")
        with self.assertRaises(h.HistoryChanged):
            await TauronClient(FakeTransport(*replies), "u", "p").fetch_history(TARGET, START, END)


if __name__ == "__main__":
    unittest.main()
