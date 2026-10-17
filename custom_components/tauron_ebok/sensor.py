"""Metadata sensors; no inferred balance or unpaid total."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TauronCoordinator

SensorValue = str | int | Decimal | date | datetime | None


@dataclass(frozen=True, kw_only=True)
class TauronSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TauronCoordinator], SensorValue]


SENSORS = (
    TauronSensorDescription(
        key="document_count",
        translation_key="document_count",
        icon="mdi:file-document-multiple",
        value_fn=lambda c: len(c.data.documents),
    ),
    TauronSensorDescription(
        key="latest_issue",
        translation_key="latest_issue",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.data.latest.issued if c.data.latest else None,
    ),
    TauronSensorDescription(
        key="latest_gross",
        translation_key="latest_gross",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="PLN",
        value_fn=lambda c: c.data.latest.gross if c.data.latest else None,
    ),
    TauronSensorDescription(
        key="latest_due",
        translation_key="latest_due",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda c: c.data.latest.due if c.data.latest else None,
    ),
    TauronSensorDescription(
        key="latest_status",
        translation_key="latest_status",
        icon="mdi:invoice-text-check",
        value_fn=lambda c: c.data.latest.status if c.data.latest else None,
    ),
    TauronSensorDescription(
        key="last_success",
        translation_key="last_success",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.last_success,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities(
        TauronSensor(entry.runtime_data, entry, description) for description in SENSORS
    )


class TauronSensor(CoordinatorEntity[TauronCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: TauronSensorDescription

    def __init__(
        self,
        coordinator: TauronCoordinator,
        entry: ConfigEntry,
        description: TauronSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TAURON eBOK",
            manufacturer="TAURON",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> SensorValue:
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key != "document_count":
            return None
        data = self.coordinator.data
        return {
            "date_from": data.date_from.isoformat(),
            "date_to": data.date_to.isoformat(),
            "account_count": data.account_count,
        }
