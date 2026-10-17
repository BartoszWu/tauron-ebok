"""Private, memory-only aiohttp session with constrained redirects."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .client import Response, TauronClient
from .models import ConnectionError, PortalChanged
from .portal import safe_url

MAX_BODY_BYTES = 4 * 1024 * 1024


class AiohttpTransport:
    def __init__(self, hass: HomeAssistant) -> None:
        # Never use HA's shared cookie jar or import the browser's session.
        self._session = async_create_clientsession(
            hass,
            cookie_jar=aiohttp.CookieJar(),
            raise_for_status=False,
            timeout=aiohttp.ClientTimeout(total=30),
        )

    def clear_cookies(self) -> None:
        self._session.cookie_jar.clear()

    async def close(self) -> None:
        # HA owns the shared connector; closing it would affect unrelated integrations.
        self._session.cookie_jar.clear()
        self._session.detach()

    async def request(
        self, method: str, url: str, *, params: dict | None = None, data: dict | None = None
    ) -> Response:
        if method not in {"GET", "POST"}:
            raise PortalChanged("Unsupported HTTP method")
        url = safe_url(url)
        if method == "POST" and urlsplit(url).hostname != "logowanie.tauron.pl":
            raise PortalChanged("POST allowed only for login")
        try:
            async with asyncio.timeout(60):
                for _ in range(10):
                    async with self._session.request(
                        method,
                        url,
                        params=params,
                        data=data,
                        allow_redirects=False,
                    ) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            location = response.headers.get("Location")
                            if not location:
                                raise PortalChanged("Missing redirect destination")
                            target = safe_url(location, str(response.url))
                            if response.status == 303 or (
                                method == "POST" and response.status in {301, 302}
                            ):
                                method, data = "GET", None
                            if (
                                data is not None
                                and urlsplit(target).hostname != urlsplit(url).hostname
                            ):
                                raise PortalChanged("Credential redirect rejected")
                            url, params = target, None
                            continue
                        body = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            body.extend(chunk)
                            if len(body) > MAX_BODY_BYTES:
                                raise PortalChanged("Portal response too large")
                        try:
                            text = body.decode(response.charset or "utf-8")
                        except (UnicodeError, LookupError):
                            raise PortalChanged("Unsupported response encoding") from None
                        return Response(response.status, str(response.url), text)
                raise PortalChanged("Too many portal redirects")
        except (aiohttp.ClientError, TimeoutError):
            # Exceptions can contain credential-bearing URLs. Never propagate or log them.
            raise ConnectionError("Cannot reach TAURON") from None


def create_client(
    hass: HomeAssistant, username: str, password: str
) -> tuple[TauronClient, AiohttpTransport]:
    transport = AiohttpTransport(hass)
    return TauronClient(transport, username, password), transport
