"""Explicitly requested metadata check, usable from the UI or an automation."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import TauronCoordinator
from .history_coordinator import HistoryCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        [
            TauronCheckDocumentsButton(entry.runtime_data, entry),
            TauronHistoryButton(entry.runtime_data.history, entry),
        ]
    )


class TauronCheckDocumentsButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "check_documents"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:file-document-refresh"

    def __init__(self, coordinator: TauronCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_check_documents"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TAURON eBOK",
            manufacturer="TAURON",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="document_check_failed"
            )


class TauronHistoryButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "refresh_history"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:chart-bar"

    def __init__(self, coordinator: HistoryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_refresh_history"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TAURON eBOK",
            manufacturer="TAURON",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_press(self) -> None:
        if self.coordinator.target is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="history_not_configured"
            )
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="history_check_failed"
            )
