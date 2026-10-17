"""Validated invoice metadata; no Home Assistant or networking dependencies."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any


class TauronError(Exception):
    """Safe error with a static message only."""


class AuthenticationError(TauronError):
    """Credentials rejected or session could not be established."""


class InteractiveLoginRequired(AuthenticationError):
    """The portal requires an unsupported interactive login step."""


class PortalChanged(TauronError):
    """Unexpected HTML, JSON, or pagination; never interpreted as no invoices."""


class ConnectionError(TauronError):
    """Transport failed without exposing request details."""


@dataclass(frozen=True, repr=False)
class Account:
    """Opaque account context discovered from the authenticated portal."""

    system: str
    customer: str
    checksum: str

    @property
    def key(self) -> str:
        return sha256(f"{self.system}:{self.customer}".encode()).hexdigest()


def number(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise PortalChanged("Invalid numeric field")
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise PortalChanged("Invalid numeric field") from None
    if not result.is_finite():
        raise PortalChanged("Invalid numeric field")
    return result


def day(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise PortalChanged("Invalid date field")
    try:
        return date.fromisoformat(value.split("T", 1)[0])
    except ValueError:
        raise PortalChanged("Invalid date field") from None


@dataclass(frozen=True, repr=False)
class Invoice:
    """No URLs, checksums, addresses, or raw JSON are retained here."""

    key: str
    number: str
    kind: str
    correction: bool | None
    issued: date | None
    due: date | None
    gross: Decimal | None
    status: str | None

    @classmethod
    def parse(cls, account: Account, raw: dict[str, Any]) -> Invoice:
        document = raw.get("DocNumber")
        kind = raw.get("DocType")
        if (
            isinstance(document, bool)
            or not isinstance(document, (str, int))
            or not str(document)
            or not isinstance(kind, str)
            or not kind
        ):
            raise PortalChanged("Missing document identity")
        signature = raw.get("DocSignature")
        correction = raw.get("IsDocumentCorrection")
        if correction is not None and not isinstance(correction, bool):
            raise PortalChanged("Invalid correction flag")
        # Provisional composite identity: its stability still needs live verification.
        identity = f"{account.key}:{kind}:{document}:{raw.get('SodId') or ''}"
        status = raw.get("Status")
        if status is not None and not isinstance(status, str):
            raise PortalChanged("Invalid payment status")
        return cls(
            key=sha256(identity.encode()).hexdigest(),
            number=str(signature or document),
            kind=str(kind),
            correction=correction,
            issued=day(raw.get("IssueDate")),
            due=day(raw.get("PaymentDate")),
            gross=number(raw.get("AmountGross")),
            status=status,
        )


@dataclass(frozen=True)
class Snapshot:
    """A complete, bounded scan, not lifetime invoice totals."""

    documents: tuple[Invoice, ...] = field(repr=False)
    account_count: int
    date_from: date
    date_to: date

    @property
    def latest(self) -> Invoice | None:
        if any(item.issued is None for item in self.documents):
            return None
        return max(
            self.documents, key=lambda item: (item.issued or date.min, item.key), default=None
        )
