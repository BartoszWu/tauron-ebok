"""HA coordinator, storage, schedule, and options with synthetic data only."""

from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from homeassistant.util.file import WriteError

from custom_components.tauron_ebok.button import TauronHistoryButton
from custom_components.tauron_ebok.config_flow import TauronOptionsFlow
from custom_components.tauron_ebok.history import HistoryAccount, HistoryChanged, Reading
from custom_components.tauron_ebok.history_coordinator import (
    HISTORY_TARGET,
    HistoryCoordinator,
    next_month,
)
from custom_components.tauron_ebok.sensor import HistorySensor

TARGET = {"system": "TEST", "billing": "synthetic-billing", "pod": "synthetic-pod"}
NOW = datetime(2026, 10, 18, 12, tzinfo=ZoneInfo("Europe/Warsaw"))
READINGS = (Reading(date(2026, 1, 22), 4, Decimal("12.3")),)


class HistoryHATests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = TemporaryDirectory()
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        self.entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="tauron_ebok",
            title="Test",
            data={"username": "synthetic-user", "password": "synthetic-pass"},
            options={HISTORY_TARGET: TARGET},
            source="user",
            unique_id="synthetic",
            discovery_keys=MappingProxyType({}),
            subentries_data=[],
        )
        self.client = SimpleNamespace(fetch_history=AsyncMock(return_value=READINGS))
        self.coordinator = HistoryCoordinator(self.hass, self.entry, self.client)
        self.clock = patch(
            "custom_components.tauron_ebok.history_coordinator.dt_util.now", return_value=NOW
        )
        self.clock.start()

    async def asyncTearDown(self):
        self.clock.stop()
        self.coordinator.async_stop()
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_import_correction_replaces_values_and_survives_real_storage_restore(self):
        await self.coordinator.async_refresh()
        self.client.fetch_history.return_value = (Reading(date(2026, 1, 22), 4, Decimal("15.5")),)
        await self.coordinator.async_refresh()
        self.assertEqual(len(self.coordinator.data), 1)
        self.assertEqual(self.coordinator.months[0]["kwh"], 15.5)
        restored = HistoryCoordinator(self.hass, self.entry, self.client)
        await restored.async_restore()
        self.assertEqual(restored.data, self.coordinator.data)
        self.assertEqual(restored.check_status, "cached")
        self.assertIsNotNone(restored.last_success)
        raw = await self.hass.async_add_executor_job(Path(restored.store.path).read_text)
        self.assertNotIn(TARGET["pod"], raw)
        self.assertNotIn(TARGET["billing"], raw)
        self.assertNotIn("synthetic-pass", raw)

    async def test_error_preserves_confirmed_snapshot_and_sensor_values(self):
        await self.coordinator.async_refresh()
        previous = self.coordinator.data
        self.client.fetch_history.side_effect = HistoryChanged("Synthetic failure")
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.data, previous)
        self.assertEqual(self.coordinator.last_error, "portal_history")
        sensor = HistorySensor(self.coordinator, self.entry, "history_total")
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.native_value, Decimal("12.3"))
        self.assertFalse(sensor.extra_state_attributes["last_check_success"])
        self.assertIsNone(sensor.state_class)

    async def test_initial_sensors_do_not_report_zero(self):
        sensor = HistorySensor(self.coordinator, self.entry, "history_total")
        self.assertIsNone(sensor.native_value)
        self.assertFalse(sensor.available)
        self.assertEqual(sensor.extra_state_attributes["months"], [])

    async def test_failed_storage_does_not_publish_new_snapshot(self):
        self.coordinator.store = SimpleNamespace(
            async_save=AsyncMock(side_effect=OSError("synthetic"))
        )
        await self.coordinator.async_refresh()
        self.assertIsNone(self.coordinator.data)
        self.assertEqual(self.coordinator.last_error, "history_storage")
        self.assertIsNone(self.coordinator.last_success)

    async def test_actual_store_swallowed_write_failure_is_detected_by_readback(self):
        await self.coordinator.async_refresh()
        previous = self.coordinator.data
        stamp = self.coordinator.last_success
        self.client.fetch_history.return_value = (Reading(date(2026, 1, 22), 4, Decimal("99")),)
        with patch.object(
            self.coordinator.store, "_write_prepared_data", side_effect=WriteError("synthetic")
        ):
            await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.data, previous)
        self.assertEqual(self.coordinator.last_success, stamp)
        self.assertEqual(self.coordinator.last_error, "history_storage")

    async def test_year_rollover_preserves_previous_year(self):
        await self.coordinator.async_refresh()
        self.client.fetch_history.return_value = (Reading(date(2027, 2, 7), 1, Decimal("20")),)
        with patch(
            "custom_components.tauron_ebok.history_coordinator.dt_util.now",
            return_value=NOW.replace(year=2027, month=2),
        ):
            await self.coordinator.async_refresh()
        self.assertEqual(self.client.fetch_history.call_args.args[1], date(2027, 1, 1))
        self.assertEqual([m["month"] for m in self.coordinator.months], ["2026-01", "2027-02"])

    async def test_disabling_history_stops_network_and_does_not_restore_other_data(self):
        entry = ConfigEntry(
            version=1,
            minor_version=1,
            domain="tauron_ebok",
            title="Disabled",
            data={},
            options={},
            source="user",
            unique_id="disabled",
            discovery_keys=MappingProxyType({}),
            subentries_data=[],
        )
        coordinator = HistoryCoordinator(self.hass, entry, self.client)
        await coordinator.async_restore()
        coordinator.async_start(self.entry)
        self.assertEqual(coordinator.check_status, "not_configured")
        self.client.fetch_history.assert_not_awaited()

    async def test_no_periodic_http_poll_and_monthly_timer_cancelled(self):
        self.coordinator.last_success = dt_util.as_utc(NOW)
        self.coordinator.coverage_start = date(2024, 1, 1)
        with patch.object(self.entry, "async_create_background_task") as create:
            self.coordinator.async_start(self.entry)
            create.assert_not_called()
        self.assertIsNone(self.coordinator.update_interval)
        self.assertIsNotNone(self.coordinator._cancel_timer)
        self.coordinator.async_stop()
        self.assertIsNone(self.coordinator._cancel_timer)
        self.client.fetch_history.assert_not_awaited()

    async def test_initial_import_or_missed_month_runs_once_in_background(self):
        tasks = []
        with patch.object(
            self.entry, "async_create_background_task", side_effect=lambda h, c, n: tasks.append(c)
        ):
            self.coordinator.async_start(self.entry)
        self.assertEqual(len(tasks), 1)
        await tasks[0]
        self.client.fetch_history.assert_awaited_once()
        self.assertEqual(self.client.fetch_history.call_args.args[1], date(2024, 1, 1))

    async def test_upgrade_backfills_once_even_when_older_years_have_no_readings(self):
        await self.coordinator.async_refresh()
        saved = await self.coordinator.store.async_load()
        saved.pop("coverage_start")  # v0.3.3 cache
        await self.coordinator.store.async_save(saved)
        restored = HistoryCoordinator(self.hass, self.entry, self.client)
        await restored.async_restore()
        self.assertIsNone(restored.coverage_start)
        self.assertEqual(restored.data, READINGS)
        await restored.async_refresh()
        self.assertEqual(self.client.fetch_history.call_args.args[1], date(2024, 1, 1))
        self.assertEqual(restored.data, READINGS)
        again = HistoryCoordinator(self.hass, self.entry, self.client)
        await again.async_restore()
        self.assertEqual(again.coverage_start, date(2024, 1, 1))
        with patch.object(self.entry, "async_create_background_task") as create:
            again.async_start(self.entry)
            create.assert_not_called()
        again.async_stop()
        await restored.async_refresh()
        self.assertEqual(self.client.fetch_history.call_args.args[1], date(2026, 1, 1))

    async def test_current_year_refresh_retains_backfilled_years(self):
        older = Reading(date(2024, 5, 11), 4, Decimal("41"))
        self.client.fetch_history.return_value = (older, *READINGS)
        await self.coordinator.async_refresh()
        self.client.fetch_history.return_value = READINGS
        await self.coordinator.async_refresh()
        self.assertEqual(self.coordinator.data, (older, *READINGS))

    async def test_monthly_callback_and_manual_button_use_same_import(self):
        await self.coordinator._monthly(NOW)
        await TauronHistoryButton(self.coordinator, self.entry).async_press()
        self.assertEqual(self.client.fetch_history.await_count, 2)
        self.assertEqual(len(self.coordinator.data), 1)

    async def test_other_selected_target_never_restores_previous_target_data(self):
        await self.coordinator.async_refresh()
        saved = await self.coordinator.store.async_load()
        saved["target_key"] = "other-point"
        restored = HistoryCoordinator(self.hass, self.entry, self.client)
        restored.store = SimpleNamespace(async_load=AsyncMock(return_value=saved))
        await restored.async_restore()
        self.assertIsNone(restored.data)

    async def test_options_save_selected_point_without_fetching_consumption(self):
        account = HistoryAccount(TARGET["system"], TARGET["billing"])
        client = SimpleNamespace(
            history_accounts=AsyncMock(return_value=(account,)),
            fetch_history=AsyncMock(),
        )
        transport = SimpleNamespace(close=AsyncMock())
        flow = TauronOptionsFlow()
        flow.hass = self.hass
        flow.handler = self.entry.entry_id
        with (
            patch.object(
                self.hass.config_entries, "async_get_known_entry", return_value=self.entry
            ),
            patch(
                "custom_components.tauron_ebok.config_flow.create_client",
                return_value=(client, transport),
            ),
        ):
            result = await flow.async_step_init({"enable_history": True})
            self.assertEqual(result["step_id"], "point")
            result = await flow.async_step_point({"point": TARGET["pod"]})
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"][HISTORY_TARGET], TARGET)
        client.fetch_history.assert_not_awaited()
        self.assertEqual(transport.close.await_count, 1)

    def test_calendar_month_boundary_including_year_and_dst(self):
        self.assertEqual(next_month(NOW).isoformat(), "2026-11-01T06:00:00+01:00")
        self.assertEqual(next_month(NOW.replace(month=12)).isoformat(), "2027-01-01T06:00:00+01:00")


if __name__ == "__main__":
    unittest.main()
