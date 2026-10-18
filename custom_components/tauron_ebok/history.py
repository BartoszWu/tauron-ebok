"""Strict adapters for portal readings, independent of HA and network access."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from html.parser import HTMLParser
from math import isfinite
from urllib.parse import parse_qs, urlsplit

from .models import PortalChanged, number
from .portal import PORTAL, safe_url

HISTORY_LIST = f"{PORTAL}/Home/Pages/historia-odczytow-licznika-prad"
DETAIL_PATH = "/Home/Pages/historia-odczytow-licznika-prad/szczegoly"
BY_POINT = f"{PORTAL}/api/sitecore/EnergyConsumption/ByPoint"
FIRST_YEAR = 2026


class HistoryChanged(PortalChanged):
    ui_error = "portal_history"


@dataclass(frozen=True, repr=False)
class HistoryAccount:
    system: str
    billing: str

    @property
    def key(self) -> str:
        return sha256(f"{self.system}:{self.billing}".encode()).hexdigest()


@dataclass(frozen=True, repr=False)
class HistoryTarget:
    system: str
    billing: str
    pod: str

    @property
    def key(self) -> str:
        return sha256(f"{self.system}:{self.billing}:{self.pod}".encode()).hexdigest()

    @classmethod
    def parse(cls, value: object) -> HistoryTarget:
        if not isinstance(value, dict) or any(
            not isinstance(value.get(k), str) or not value[k].strip()
            for k in ("system", "billing", "pod")
        ):
            raise HistoryChanged("Invalid history selection")
        return cls(value["system"], value["billing"], value["pod"])


class HistoryHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.accounts: dict[str, HistoryAccount] = {}
        self.details: list[tuple[HistoryAccount, str]] = []
        self.billing: str | None = None
        self.endpoint: str | None = None
        self.points: dict[str, str] = {}
        self.anchor_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "a" and a.get("href"):
            self.anchor_count += 1
            # This portal still emits absolute http navigation links. Upgrade only
            # same-host links before validating; never permit a credential redirect.
            href = a["href"]
            if href.startswith("http://moj.tauron.pl/"):
                href = "https://" + href[len("http://") :]
            parsed = urlsplit(href)
            # Sitecore may emit a relative path under /Home/Pages/.
            if not parsed.scheme and not href.startswith(("/", "#", "?")):
                href = safe_url(href, f"{PORTAL}/Home/Pages/")
                parsed = urlsplit(href)
            if parsed.path.casefold() in {
                "/home/pages/faktury",
                "/home/pages/umowa",
                DETAIL_PATH.casefold(),
            }:
                url = safe_url(href)
                q = parse_qs(urlsplit(url).query)
                customer_key = "cid" if parsed.path.casefold() == DETAIL_PATH.casefold() else "pp"
                if len(q.get("sid", [])) == 1 and len(q.get(customer_key, [])) == 1:
                    account = HistoryAccount(q["sid"][0], q[customer_key][0])
                    if account.system and account.billing:
                        if customer_key == "cid":
                            self.details.append((account, url))
                        else:
                            self.accounts[account.key] = account
        if a.get("id") == "historia-odczytow":
            self.billing = a.get("data-billing-number")
            self.endpoint = safe_url(a.get("data-api-by-point") or "")
            if a.get("data-unit") != "kWh" or "data-append-params" not in a:
                raise HistoryChanged("Unsupported history units or mode")
        if tag == "input" and "point-checkbox" in (a.get("class") or "").split():
            pod = a.get("data-point-id")
            if pod:
                label = a.get("data-point-address") or a.get("data-point-number") or pod
                if pod in self.points and self.points[pod] != label:
                    raise HistoryChanged("Conflicting point metadata")
                self.points[pod] = label


def history_html(html: str) -> HistoryHTML:
    parser = HistoryHTML()
    parser.feed(html)
    return parser


@dataclass(frozen=True)
class Reading:
    day: date
    kind: int
    kwh: Decimal | None

    def serialize(self) -> dict:
        return {
            "date": self.day.isoformat(),
            "type": self.kind,
            "kwh": str(self.kwh) if self.kwh is not None else None,
        }


def parse_readings(
    payload: object, target: HistoryTarget, start: date, end: date
) -> tuple[Reading, ...]:
    # A selected POD must never silently turn into an aggregate of all contracts.
    if not isinstance(payload, list) or len(payload) != 1:
        raise HistoryChanged("Expected exactly one selected point")
    point = payload[0]
    if not isinstance(point, dict) or str(point.get("Number")) != target.pod:
        raise HistoryChanged("Unexpected history point")
    rows = point.get("Readings")
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise HistoryChanged("Missing or excessive history readings")
    result: dict[tuple[date, int], Reading] = {}
    for row in rows:
        if not isinstance(row, dict) or type(row.get("Type")) is not int:
            raise HistoryChanged("Invalid reading type")
        try:
            raw_date = row["LastReadingDate"]
            if not isinstance(raw_date, str):
                raise ValueError
            d, m, y = raw_date.split(".")
            day = date(int(y), int(m), int(d))
        except (KeyError, TypeError, ValueError):
            raise HistoryChanged("Invalid reading date") from None
        if not start <= day <= end:
            raise HistoryChanged("Reading outside requested range")
        details = row.get("Details")
        if not isinstance(details, list) or not details:
            raise HistoryChanged("Missing reading details")
        zones: dict[str, Decimal | None] = {}
        for detail in details:
            if not isinstance(detail, dict) or not isinstance(detail.get("Zone"), str):
                raise HistoryChanged("Invalid consumption zone")
            zone = detail["Zone"]
            if not zone or zone in zones or "Consumption" not in detail:
                raise HistoryChanged("Ambiguous consumption zone")
            value = number(detail["Consumption"])
            if value is not None and (value < 0 or not isfinite(float(value))):
                raise HistoryChanged("Invalid consumption range")
            zones[zone] = value
        # Do not add an aggregated zone a second time to its component zones.
        values = [zones["AggregatedZone"]] if "AggregatedZone" in zones else list(zones.values())
        kwh = None if any(v is None for v in values) else sum(values, Decimal(0))
        key = (day, row["Type"])
        if key in result:
            raise HistoryChanged("Duplicate reading")
        result[key] = Reading(day, row["Type"], kwh)
    return tuple(sorted(result.values(), key=lambda r: (r.day, r.kind)))


def monthly_rows(readings: tuple[Reading, ...]) -> list[dict]:
    groups: dict[str, list[Reading]] = {}
    for reading in readings:
        groups.setdefault(reading.day.strftime("%Y-%m"), []).append(reading)
    return [
        {
            "month": month,
            "kwh": float(sum((r.kwh for r in rows), Decimal(0)))
            if all(r.kwh is not None for r in rows)
            else None,
            "reading_dates": sorted({r.day.isoformat() for r in rows}),
        }
        for month, rows in sorted(groups.items())
    ]
