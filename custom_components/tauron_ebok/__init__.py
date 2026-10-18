"""TAURON eBOK invoice metadata integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .coordinator import TauronCoordinator
from .history_coordinator import HistoryCoordinator
from .transport import create_client

PLATFORMS = [Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    client, transport = create_client(hass, entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD])
    coordinator = TauronCoordinator(hass, entry, client, transport)
    coordinator.history = HistoryCoordinator(hass, entry, client)
    try:
        # Credentials were validated by the config flow. Invoice errors must not
        # prevent entry setup or saving the credentials, including after restart.
        entry.runtime_data = coordinator
        await coordinator.history.async_restore()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_options_updated))
        coordinator.history.async_start(entry)
    except BaseException:
        await transport.close()
        raise
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        entry.runtime_data.history.async_stop()
        await entry.runtime_data.transport.close()
        return True
    return False


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove our own history only when the user removes this integration entry."""
    from homeassistant.helpers.storage import Store

    from .const import DOMAIN

    await Store(hass, 1, f"{DOMAIN}.history.{entry.entry_id}").async_remove()
