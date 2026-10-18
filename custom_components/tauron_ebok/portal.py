"""HTML discovery isolated from transport. Portal selectors need live validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from .models import Account, InteractiveLoginRequired, PortalChanged

PORTAL = "https://moj.tauron.pl"
LOGIN = "https://logowanie.tauron.pl/login"
ARCHIVE = f"{PORTAL}/Home/Pages/archiwum-dokumentow"
DOCUMENTS = f"{PORTAL}/api/sitecore/EbokArchiveDocuments/GetDocuments"
ALLOWED_HOSTS = frozenset({"moj.tauron.pl", "logowanie.tauron.pl"})


class SessionMarkerParser(HTMLParser):
    """Recognize the logout link observed on authenticated portal pages."""

    def __init__(self) -> None:
        super().__init__()
        self.authenticated = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        parsed = urlsplit(urljoin(PORTAL, href))
        if (
            parsed.scheme == "https"
            and parsed.hostname == "moj.tauron.pl"
            and parsed.path.casefold() == "/api/sitecore/home/logout"
        ):
            self.authenticated = True


def has_session_marker(html: str) -> bool:
    parser = SessionMarkerParser()
    parser.feed(html)
    return parser.authenticated


def safe_url(value: str, base: str = PORTAL) -> str:
    try:
        result = urljoin(base, value)
        parsed = urlsplit(result)
        port = parsed.port
    except ValueError:
        raise PortalChanged("Invalid portal destination") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise PortalChanged("Unexpected portal destination")
    return result


@dataclass(repr=False)
class LoginForm:
    action: str
    hidden: dict[str, str] = field(default_factory=dict)


class LoginParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.form: LoginForm | None = None
        self.in_form = False
        self.interactive = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        marker = " ".join(str(data.get(k, "")) for k in ("id", "class", "name", "src"))
        if any(
            x in marker.lower()
            for x in ("g-recaptcha", "h-captcha", "hcaptcha", "kc-otp", "webauthn")
        ):
            self.interactive = True
        if tag == "input" and data.get("name") in {"otp", "totp"}:
            self.interactive = True
        if tag == "form":
            self.in_form = data.get("id") == "kc-form-login"
            if self.in_form:
                if not data.get("action") or data.get("method", "get").lower() != "post":
                    raise PortalChanged("Unsupported login form")
                self.form = LoginForm(data["action"])
        if tag == "input" and self.in_form and self.form is not None:
            if data.get("type", "").lower() == "hidden" and data.get("name"):
                self.form.hidden[data["name"]] = data.get("value") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.in_form = False


def login_form(html: str, base: str) -> LoginForm | None:
    parser = LoginParser()
    parser.feed(html)
    if parser.interactive:
        raise InteractiveLoginRequired("Interactive login required")
    if parser.form is not None:
        parser.form.action = safe_url(parser.form.action, base)
        if urlsplit(parser.form.action).hostname != "logowanie.tauron.pl":
            raise PortalChanged("Unexpected login destination")
    return parser.form


def _account(values: dict) -> Account | None:
    # Accept complete contexts only. Never join customer/checksum values across rows.
    normalized = {
        str(k).lower().replace("data-", "").replace("-", "").replace("_", ""): v
        for k, v in values.items()
    }
    customer = normalized.get("clientnumber") or normalized.get("customernumber")
    # The archive's dataAjaxFillTable reads data-client-checksum. The API uses
    # clientNumberCheckSum; retain that spelling for JSON/query contexts too.
    checksum = normalized.get("clientnumberchecksum") or normalized.get("clientchecksum")
    system = normalized.get("system")
    if all(
        isinstance(v, (str, int)) and not isinstance(v, bool) and str(v)
        for v in (customer, checksum, system)
    ):
        return Account(str(system), str(customer), str(checksum))
    return None


class AccountsParser(HTMLParser):
    """Recognize complete attributes, API query links, and JSON configuration nodes.

    These encodings are adapters, not claims about the current live portal markup.
    Unknown markup fails closed and requires an adapter update.
    """

    def __init__(self) -> None:
        super().__init__()
        self.accounts: dict[str, Account] = {}
        self.in_json_script = False
        self.script: list[str] = []

    def add(self, account: Account | None) -> None:
        if account is not None:
            previous = self.accounts.get(account.key)
            if previous is not None and previous != account:
                raise PortalChanged("Conflicting account contexts")
            self.accounts[account.key] = account

    def walk_json(self, value: object) -> None:
        if isinstance(value, dict):
            self.add(_account(value))
            for child in value.values():
                self.walk_json(child)
        elif isinstance(value, list):
            for child in value:
                self.walk_json(child)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = dict(attrs)
        self.add(_account(data))
        if tag == "script" and data.get("type") == "application/json":
            self.in_json_script = True
            self.script = []
        for key, value in data.items():
            if not value:
                continue
            if key in {"href", "data-url"}:
                parsed = urlsplit(urljoin(PORTAL, value))
                if parsed.hostname == "moj.tauron.pl":
                    query = parse_qs(parsed.query)
                    self.add(_account({k: v[0] for k, v in query.items() if len(v) == 1}))
            elif value.lstrip().startswith(("{", "[")):
                try:
                    self.walk_json(json.loads(value))
                except (ValueError, RecursionError):
                    continue

    def handle_data(self, data: str) -> None:
        if self.in_json_script:
            self.script.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self.in_json_script:
            self.in_json_script = False
            try:
                self.walk_json(json.loads("".join(self.script)))
            except (ValueError, RecursionError):
                return


def discover_accounts(html: str) -> tuple[Account, ...]:
    parser = AccountsParser()
    parser.feed(html)
    if not parser.accounts:
        raise PortalChanged("Cannot discover invoice account contexts")
    return tuple(parser.accounts.values())
