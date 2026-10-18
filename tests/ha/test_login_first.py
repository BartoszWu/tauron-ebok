"""Real HA classes, synthetic credentials/client, and no portal traffic.

Run separately from the dependency-free client tests:
python -m unittest discover -s tests/ha -v
"""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.tauron_ebok import async_setup_entry, async_unload_entry
from custom_components.tauron_ebok.button import TauronCheckDocumentsButton
from custom_components.tauron_ebok.config_flow import TauronConfigFlow
from custom_components.tauron_ebok.coordinator import TauronCoordinator
from custom_components.tauron_ebok.models import AuthenticationError, DocumentsPageChanged, Snapshot
from custom_components.tauron_ebok.sensor import SENSORS, TauronSensor

CREDS = {"username": "synthetic-user", "password": "synthetic-password"}


class LoginFirstTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="tauron_ebok",
            title="TAURON eBOK",
            data=CREDS,
            options={},
            source="user",
            unique_id="synthetic",
            discovery_keys=MappingProxyType({}),
            subentries_data=[],
        )
        self.client = SimpleNamespace(verify_login=AsyncMock(), fetch=AsyncMock())
        self.transport = SimpleNamespace(close=AsyncMock())

    async def asyncTearDown(self):
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_config_flow_creates_entry_when_documents_would_fail(self):
        self.client.fetch.side_effect = DocumentsPageChanged("Synthetic document failure")
        flow = TauronConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "user"}
        with patch(
            "custom_components.tauron_ebok.config_flow.create_client",
            return_value=(self.client, self.transport),
        ):
            result = await flow.async_step_user(dict(CREDS))
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"], CREDS)
        self.client.verify_login.assert_awaited_once()
        self.client.fetch.assert_not_awaited()
        self.transport.close.assert_awaited_once()

    async def test_invalid_login_does_not_create_entry(self):
        self.client.verify_login.side_effect = AuthenticationError("Synthetic rejection")
        flow = TauronConfigFlow()
        flow.hass = self.hass
        flow.context = {"source": "user"}
        with patch(
            "custom_components.tauron_ebok.config_flow.create_client",
            return_value=(self.client, self.transport),
        ):
            result = await flow.async_step_user(dict(CREDS))
        self.assertEqual(result["type"], "form")
        self.assertEqual(result["errors"]["base"], "invalid_auth")
        self.client.fetch.assert_not_awaited()

    async def test_setup_and_unload_do_not_fetch_documents(self):
        with (
            patch(
                "custom_components.tauron_ebok.create_client",
                return_value=(self.client, self.transport),
            ),
            patch.object(
                self.hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
            ) as forward,
        ):
            self.assertTrue(await async_setup_entry(self.hass, self.entry))
        self.client.fetch.assert_not_awaited()
        self.client.verify_login.assert_not_awaited()
        self.assertIsNone(self.entry.runtime_data.update_interval)
        self.assertEqual(self.entry.runtime_data.check_status, "not_checked")
        self.assertEqual(set(forward.call_args.args[1]), {"sensor", "button"})
        with patch.object(
            self.hass.config_entries,
            "async_unload_platforms",
            new_callable=AsyncMock,
            return_value=True,
        ):
            self.assertTrue(await async_unload_entry(self.hass, self.entry))
        self.transport.close.assert_awaited_once()

    def coordinator(self):
        return TauronCoordinator(self.hass, self.entry, self.client, self.transport)

    async def test_button_failure_keeps_credentials_and_exposes_status(self):
        coordinator = self.coordinator()
        self.client.fetch.side_effect = DocumentsPageChanged("Synthetic document failure")
        button = TauronCheckDocumentsButton(coordinator, self.entry)
        with self.assertRaises(HomeAssistantError):
            await button.async_press()
        self.assertEqual(coordinator.check_status, "failed")
        self.assertEqual(coordinator.last_error, "portal_documents")
        self.assertEqual(dict(self.entry.data), CREDS)
        self.assertIsNone(coordinator.data)
        self.client.fetch.assert_awaited_once()
        self.assertIsNone(coordinator.update_interval)

    async def test_button_success_updates_sensors_and_can_retry_after_failure(self):
        coordinator = self.coordinator()
        button = TauronCheckDocumentsButton(coordinator, self.entry)
        today = date(2026, 10, 18)
        snapshot = Snapshot((), 1, today, today)
        self.client.fetch.side_effect = [DocumentsPageChanged("Synthetic"), snapshot]
        with self.assertRaises(HomeAssistantError):
            await button.async_press()
        await button.async_press()
        self.assertEqual(coordinator.data, snapshot)
        self.assertEqual(coordinator.check_status, "success")
        self.assertIsNone(coordinator.last_error)
        self.assertIsNotNone(coordinator.last_success)
        self.assertEqual(self.client.fetch.await_count, 2)
        self.assertEqual(dict(self.entry.data), CREDS)

    async def test_sensors_handle_no_scan_without_showing_zero_documents(self):
        coordinator = self.coordinator()
        sensors = {
            description.key: TauronSensor(coordinator, self.entry, description)
            for description in SENSORS
        }
        self.assertEqual(sensors["check_status"].native_value, "not_checked")
        self.assertTrue(sensors["check_status"].available)
        self.assertIsNone(sensors["document_count"].native_value)
        self.assertFalse(sensors["document_count"].available)
        self.assertIsNone(sensors["document_count"].extra_state_attributes)
        self.assertIsNone(sensors["last_success"].native_value)
        self.assertTrue(sensors["last_success"].available)


if __name__ == "__main__":
    unittest.main()
