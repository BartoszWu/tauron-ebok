"""Test redirect and credential controls with a synthetic session, without aiohttp/HA."""

from __future__ import annotations

import importlib
import sys
import unittest
from types import ModuleType
from unittest.mock import Mock, patch

from test_client import models, portal


class SyntheticClientError(Exception):
    pass


aiohttp = ModuleType("aiohttp")
aiohttp.ClientError = SyntheticClientError
core = ModuleType("homeassistant.core")
core.HomeAssistant = object
helper = ModuleType("homeassistant.helpers.aiohttp_client")
helper.async_create_clientsession = Mock()
with patch.dict(
    sys.modules,
    {
        "aiohttp": aiohttp,
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.core": core,
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.aiohttp_client": helper,
    },
):
    transport_module = importlib.import_module("tauron_ebok.transport")


class Stream:
    def __init__(self, body):
        self.body = body

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]


class HTTPResponse:
    def __init__(self, status=200, url=portal.ARCHIVE, location=None, body=b"ok"):
        self.status = status
        self.url = url
        self.headers = {"Location": location} if location else {}
        self.content = Stream(body)
        self.charset = "utf-8"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.cookie_jar = Mock()
        self.detach = Mock()

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected request")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def transport(*responses):
    instance = transport_module.AiohttpTransport.__new__(transport_module.AiohttpTransport)
    instance._session = Session(*responses)
    return instance


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_to_untrusted_host_rejected_before_second_request(self):
        instance = transport(HTTPResponse(302, location="https://example.test/path"))
        with self.assertRaises(models.PortalChanged):
            await instance.request("GET", portal.ARCHIVE)
        self.assertEqual(len(instance._session.calls), 1)

    async def test_credentials_not_forwarded_by_cross_host_307(self):
        instance = transport(HTTPResponse(307, portal.LOGIN, portal.ARCHIVE))
        with self.assertRaises(models.PortalChanged):
            await instance.request("POST", portal.LOGIN, data={"password": "synthetic-secret"})
        self.assertEqual(len(instance._session.calls), 1)

    async def test_post_302_changes_to_get_and_drops_credentials(self):
        instance = transport(HTTPResponse(302, portal.LOGIN, portal.ARCHIVE), HTTPResponse())
        await instance.request("POST", portal.LOGIN, data={"password": "synthetic-secret"})
        method, url, kwargs = instance._session.calls[1]
        self.assertEqual(method, "GET")
        self.assertEqual(url, portal.ARCHIVE)
        self.assertIsNone(kwargs["data"])

    async def test_same_host_307_can_preserve_login_post(self):
        target = "https://logowanie.tauron.pl/realms/test/login"
        instance = transport(HTTPResponse(307, portal.LOGIN, target), HTTPResponse(url=target))
        await instance.request("POST", portal.LOGIN, data={"password": "synthetic-secret"})
        self.assertEqual(instance._session.calls[1][0], "POST")

    async def test_post_to_invoice_api_rejected(self):
        instance = transport()
        with self.assertRaises(models.PortalChanged):
            await instance.request("POST", portal.DOCUMENTS, data={})
        self.assertEqual(instance._session.calls, [])

    async def test_mutation_methods_rejected(self):
        for method in ("PUT", "PATCH", "DELETE"):
            instance = transport()
            with self.subTest(method=method), self.assertRaises(models.PortalChanged):
                await instance.request(method, portal.ARCHIVE)
            self.assertEqual(instance._session.calls, [])

    async def test_oversize_response_rejected(self):
        instance = transport(HTTPResponse(body=b"x" * 20))
        with patch.object(transport_module, "MAX_BODY_BYTES", 10):
            with self.assertRaises(models.PortalChanged):
                await instance.request("GET", portal.ARCHIVE)

    async def test_exception_does_not_expose_request_url(self):
        instance = transport(
            SyntheticClientError("https://example.test/?password=synthetic-secret")
        )
        with self.assertRaises(models.ConnectionError) as result:
            await instance.request("GET", portal.ARCHIVE)
        self.assertNotIn("synthetic-secret", str(result.exception))
        self.assertTrue(result.exception.__suppress_context__)

    async def test_detach_does_not_close_shared_connector(self):
        instance = transport()
        await instance.close()
        instance._session.detach.assert_called_once()
        instance._session.cookie_jar.clear.assert_called_once()

    async def test_invalid_port_is_safe_error(self):
        with self.assertRaises(models.PortalChanged):
            await transport().request("GET", "https://moj.tauron.pl:invalid/path")

    async def test_redirect_loop_is_bounded(self):
        instance = transport(*(HTTPResponse(302, location=portal.ARCHIVE) for _ in range(10)))
        with self.assertRaises(models.PortalChanged):
            await instance.request("GET", portal.ARCHIVE)
        self.assertEqual(len(instance._session.calls), 10)
