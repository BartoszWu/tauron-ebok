"""HTTP-only invoice client; POST is restricted to the SSO login form."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol
from urllib.parse import urlsplit

from .history import (
    BY_POINT,
    HistoryAccount,
    HistoryChanged,
    HistoryTarget,
    Reading,
    history_html,
    parse_readings,
)
from .models import (
    Account,
    ArchivePageChanged,
    AuthenticationError,
    ConnectionError,
    DocumentsPageChanged,
    InteractiveLoginRequired,
    Invoice,
    LoginPageChanged,
    PortalChanged,
    Snapshot,
)
from .portal import (
    ARCHIVE,
    DOCUMENTS,
    LOGIN,
    PORTAL,
    discover_accounts,
    has_session_marker,
    login_form,
)

PAGE_SIZE = 5  # Verified portal range, not an assumed larger server limit.
MAX_PAGES = 100
LOOKBACK_DAYS = 180


@dataclass(frozen=True, repr=False)
class Response:
    status: int
    url: str
    body: str


class Transport(Protocol):
    async def request(
        self, method: str, url: str, *, params: dict | None = None, data: dict | None = None
    ) -> Response: ...

    def clear_cookies(self) -> None: ...


class TauronClient:
    def __init__(self, transport: Transport, username: str, password: str) -> None:
        self._transport = transport
        self._username = username
        self._password = password
        self._lock = asyncio.Lock()

    def _authenticated(self, response: Response) -> None:
        if response.status in (401, 403):
            raise AuthenticationError("Session expired")
        if response.status != 200:
            raise ConnectionError("Portal request failed")
        if login_form(response.body, response.url) is not None:
            raise AuthenticationError("Session expired")
        if urlsplit(response.url).hostname != urlsplit(PORTAL).hostname:
            raise AuthenticationError("Session expired")

    async def _login(self) -> None:
        self._transport.clear_cookies()
        response = await self._transport.request("GET", LOGIN, params={"service": PORTAL})
        if response.status != 200:
            raise ConnectionError("Login page unavailable")
        form = login_form(response.body, response.url)
        if form is None:
            raise PortalChanged("Login form not recognized")
        payload = {**form.hidden, "username": self._username, "password": self._password}
        response = await self._transport.request("POST", form.action, data=payload)
        self._authenticated(response)

    async def verify_login(self) -> None:
        """Validate credentials without depending on contracts or invoice APIs."""
        async with self._lock:
            try:
                await self._login()
                response = await self._transport.request("GET", PORTAL)
                self._authenticated(response)
                if not has_session_marker(response.body):
                    raise LoginPageChanged("Authenticated page not confirmed")
            except PortalChanged:
                raise LoginPageChanged("Authenticated page not confirmed") from None

    async def _history_request(self, operation):
        """Share one session/lock; retry only after authentication loss."""
        async with self._lock:
            for attempt in range(2):
                try:
                    return await operation()
                except InteractiveLoginRequired:
                    raise
                except AuthenticationError:
                    if attempt:
                        raise
                    await self._login()
                except HistoryChanged:
                    raise
                except PortalChanged:
                    raise HistoryChanged("History response not recognized") from None
        raise AuthenticationError("Cannot establish session")

    async def _history_accounts(self) -> tuple[HistoryAccount, ...]:
        response = await self._transport.request("GET", PORTAL)
        self._authenticated(response)
        if not has_session_marker(response.body):
            raise HistoryChanged("Authenticated page not confirmed")
        accounts = tuple(history_html(response.body).accounts.values())
        if not accounts:
            raise HistoryChanged("No active history account found")
        return accounts

    async def history_accounts(self) -> tuple[HistoryAccount, ...]:
        return await self._history_request(self._history_accounts)

    async def fetch_history(
        self, target: HistoryTarget, start: date, end: date
    ) -> tuple[Reading, ...]:
        if start > end or start.year < 2026 or end.year - start.year > 10:
            raise HistoryChanged("Unsupported history range")

        async def operation():
            if HistoryAccount(target.system, target.billing) not in await self._history_accounts():
                raise HistoryChanged("Selected account is no longer active")
            response = await self._transport.request(
                "GET",
                BY_POINT,
                params={
                    "billingNumber": target.billing,
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "Pods": target.pod,
                },
            )
            self._authenticated(response)
            try:
                payload = json.loads(response.body)
            except ValueError:
                raise HistoryChanged("Invalid history JSON") from None
            return parse_readings(payload, target, start, end)

        return await self._history_request(operation)

    async def fetch(self, today: date) -> Snapshot:
        """Retry a complete scan once after authentication loss; never publish partial data."""
        async with self._lock:
            for attempt in range(2):
                try:
                    return await self._scan(today)
                except InteractiveLoginRequired:
                    raise
                except AuthenticationError:
                    if attempt:
                        raise
                    try:
                        await self._login()
                    except PortalChanged:
                        raise LoginPageChanged("Login response not recognized") from None
        raise AuthenticationError("Cannot establish session")

    async def _scan(self, today: date) -> Snapshot:
        try:
            response = await self._transport.request("GET", ARCHIVE)
            self._authenticated(response)
            accounts = discover_accounts(response.body)
        except PortalChanged:
            raise ArchivePageChanged("Archive response not recognized") from None
        start = today - timedelta(days=LOOKBACK_DAYS)
        documents: dict[str, Invoice] = {}
        for account in accounts:
            try:
                account_documents = await self._list(account, start, today)
            except PortalChanged:
                raise DocumentsPageChanged("Document response not recognized") from None
            for document in account_documents:
                previous = documents.get(document.key)
                if previous is not None and previous != document:
                    raise DocumentsPageChanged("Conflicting duplicate document")
                documents[document.key] = document
        return Snapshot(tuple(documents.values()), len(accounts), start, today)

    async def _list(self, account: Account, start: date, end: date) -> list[Invoice]:
        documents: dict[str, Invoice] = {}
        expected_total: int | None = None
        for page in range(MAX_PAGES):
            first = page * PAGE_SIZE + 1
            params = {
                "rangeFrom": first,
                "rangeTo": first + PAGE_SIZE - 1,
                "clientNumber": account.customer,
                "clientNumberCheckSum": account.checksum,
                "system": account.system,
                "from": start.strftime("%d.%m.%Y"),
                "to": end.strftime("%d.%m.%Y"),
                "docKind": "All",
                "docNumber": "",
            }
            response = await self._transport.request("GET", DOCUMENTS, params=params)
            self._authenticated(response)
            try:
                payload = json.loads(response.body)
            except ValueError:
                raise PortalChanged("Invalid document JSON") from None
            if (
                not isinstance(payload, dict)
                or payload.get("Status") != "OK"
                or payload.get("StatusEnum") != 0
            ):
                raise PortalChanged("Unsuccessful document response")
            rows = payload.get("Documents")
            total = payload.get("TotalNumber")
            if not isinstance(rows, list) or type(total) is not int or total < 0:
                raise PortalChanged("Invalid document page")
            if payload.get("RangeFrom") != first or payload.get("RangeTo") != first + PAGE_SIZE - 1:
                raise PortalChanged("Unexpected document range")
            if expected_total is not None and total != expected_total:
                raise PortalChanged("Document list changed during scan")
            expected_total = total
            expected_rows = min(PAGE_SIZE, max(0, total - first + 1))
            if len(rows) != expected_rows:
                raise PortalChanged("Incomplete document page")
            for row in rows:
                if not isinstance(row, dict) or str(row.get("CustomerNumber")) != account.customer:
                    raise PortalChanged("Unexpected document owner")
                document = Invoice.parse(account, row)
                if document.key in documents:
                    raise PortalChanged("Duplicate across document pages")
                documents[document.key] = document
            if first + len(rows) > total:
                return list(documents.values())
        raise PortalChanged("Document pagination limit exceeded")
