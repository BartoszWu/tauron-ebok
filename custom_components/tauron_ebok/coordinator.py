"""One complete scan daily; failed scans leave entities unavailable."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import TauronClient
from .const import DOMAIN, UPDATE_INTERVAL
from .models import AuthenticationError, ConnectionError, PortalChanged, Snapshot
from .transport import AiohttpTransport

_LOGGER = logging.getLogger(__name__)


class TauronCoordinator(DataUpdateCoordinator[Snapshot]):
    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TauronClient,
        transport: AiohttpTransport,
    ) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=UPDATE_INTERVAL
        )
        self.client = client
        self.transport = transport
        self.last_success: datetime | None = None

    async def _async_update_data(self) -> Snapshot:
        try:
            snapshot = await self.client.fetch(dt_util.now().date())
        except AuthenticationError:
            raise ConfigEntryAuthFailed("TAURON authentication requires attention") from None
        except ConnectionError:
            raise UpdateFailed("Cannot reach TAURON") from None
        except PortalChanged:
            raise UpdateFailed("TAURON response not recognized; no complete scan") from None
        self.last_success = dt_util.utcnow()
        return snapshot
