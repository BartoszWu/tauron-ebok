"""Persistent reading history with one calendar-month refresh, independent of invoices."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import TauronClient
from .const import DOMAIN
from .history import FIRST_YEAR, HistoryTarget, Reading, monthly_rows
from .models import AuthenticationError, ConnectionError, PortalChanged, number

_LOGGER = logging.getLogger(__name__)
HISTORY_TARGET = "history_target"


def next_month(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return now.replace(year=year, month=month, day=1, hour=6, minute=0, second=0, microsecond=0)


class HistoryCoordinator(DataUpdateCoordinator[tuple[Reading, ...]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: TauronClient) -> None:
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN} history", config_entry=entry, update_interval=None
        )
        self.client = client
        self.target = (
            HistoryTarget.parse(dict(entry.options[HISTORY_TARGET]))
            if entry.options.get(HISTORY_TARGET)
            else None
        )
        self.store = Store(
            hass, 1, f"{DOMAIN}.history.{entry.entry_id}", private=True, atomic_writes=True
        )
        self.coverage_start: date | None = None
        self.last_success: datetime | None = None
        self.check_status = "not_checked" if self.target else "not_configured"
        self.last_error: str | None = None
        self._cancel_timer = None
        self._stopped = False
        self._import_lock = asyncio.Lock()

    @property
    def months(self) -> list[dict]:
        return monthly_rows(self.data) if self.data is not None else []

    async def async_restore(self) -> None:
        if self.target is None:
            return
        try:
            saved = await self.store.async_load()
            if not isinstance(saved, dict) or saved.get("target_key") != self.target.key:
                return
            records = saved["readings"]
            stamp = dt_util.parse_datetime(saved["last_success"])
            if (
                stamp is None
                or stamp.tzinfo is None
                or not isinstance(records, list)
                or not records
                or len(records) > 10000
            ):
                raise ValueError
            parsed = []
            keys = set()
            for row in records:
                if not isinstance(row, dict):
                    raise ValueError
                day = date.fromisoformat(row["date"])
                value = number(row["kwh"])
                kind = row["type"]
                if (
                    type(kind) is not int
                    or day.year < FIRST_YEAR
                    or (value is not None and value < 0)
                    or (day, kind) in keys
                ):
                    raise ValueError
                keys.add((day, kind))
                parsed.append(Reading(day, kind, value))
            coverage = saved.get("coverage_start")
            coverage_start = date.fromisoformat(coverage) if coverage else None
            if coverage_start is not None and coverage_start > stamp.date():
                raise ValueError
            self.data = tuple(sorted(parsed, key=lambda r: (r.day, r.kind)))
            self.coverage_start = coverage_start
            self.last_success = stamp
            self.check_status = "cached"
        except (KeyError, TypeError, ValueError, PortalChanged):
            self.last_error = "history_cache"

    def async_start(self, entry: ConfigEntry) -> None:
        if self.target is None:
            return
        self._stopped = False
        entry.async_on_unload(self.async_stop)
        now = dt_util.now()
        # Fresh cache means no portal read on restart. A missed month catches up once.
        if (
            self.coverage_start is None
            or self.coverage_start.year > FIRST_YEAR
            or self.last_success is None
            or (dt_util.as_local(self.last_success).strftime("%Y-%m") != now.strftime("%Y-%m"))
        ):
            entry.async_create_background_task(
                self.hass, self.async_refresh(), "tauron_ebok initial history import"
            )
        self._schedule(now)

    @callback
    def async_stop(self) -> None:
        self._stopped = True
        if self._cancel_timer:
            self._cancel_timer()
            self._cancel_timer = None

    def _schedule(self, now: datetime) -> None:
        if self._cancel_timer:
            self._cancel_timer()
        self._cancel_timer = async_track_point_in_time(self.hass, self._monthly, next_month(now))

    async def _monthly(self, now: datetime) -> None:
        try:
            await self.async_refresh()
        finally:
            if not self._stopped:
                self._schedule(dt_util.as_local(now))

    async def _async_update_data(self) -> tuple[Reading, ...]:
        async with self._import_lock:
            return await self._import()

    async def _import(self) -> tuple[Reading, ...]:
        if self.target is None:
            raise UpdateFailed("Select a consumption point in integration options")
        self.check_status, self.last_error = "checking", None
        self.async_update_listeners()
        today = dt_util.now().date()
        # Backfill from 2024 once, including upgrades of an existing 2026 cache.
        # Subsequently refresh the current year;
        # January also revisits December, when delayed readings can still arrive.
        backfill = self.coverage_start is None or self.coverage_start.year > FIRST_YEAR
        year = FIRST_YEAR if backfill else today.year - (today.month == 1)
        start = date(max(FIRST_YEAR, year), 1, 1)
        try:
            readings = await self.client.fetch_history(self.target, start, today)
            retained = [r for r in self.data or () if not start <= r.day <= today]
            updated = tuple(sorted((*retained, *readings), key=lambda r: (r.day, r.kind)))
            stamp = dt_util.utcnow()
            # Commit storage before publishing so a failed save keeps the old snapshot.
            saved = {
                "target_key": self.target.key,
                "coverage_start": min(start, self.coverage_start or start).isoformat(),
                "last_success": stamp.isoformat(),
                "readings": [r.serialize() for r in updated],
            }
            await self.store.async_save(saved)
            # HA Store logs some disk failures rather than raising them. Confirm
            # the persisted snapshot via its public read API before publishing.
            if await self.store.async_load() != saved:
                raise OSError("History storage verification failed")
        except AuthenticationError:
            self.check_status, self.last_error = "failed", "invalid_auth"
            raise ConfigEntryAuthFailed("TAURON authentication requires attention") from None
        except ConnectionError:
            self.check_status, self.last_error = "failed", "cannot_connect"
            raise UpdateFailed("Cannot reach TAURON") from None
        except PortalChanged:
            self.check_status, self.last_error = "failed", "portal_history"
            raise UpdateFailed("TAURON history response not recognized") from None
        except OSError:
            self.check_status, self.last_error = "failed", "history_storage"
            raise UpdateFailed("Cannot save TAURON history") from None
        self.coverage_start = min(start, self.coverage_start or start)
        self.last_success = stamp
        self.check_status = "success"
        return updated
