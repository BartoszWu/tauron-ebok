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
from .history_coordinator import HistoryCoordinator

SensorValue = str | int | Decimal | date | datetime | None


@dataclass(frozen=True, kw_only=True)
class TauronSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TauronCoordinator], SensorValue]


SENSORS = (
    TauronSensorDescription(
        key="check_status",
        translation_key="check_status",
        device_class=SensorDeviceClass.ENUM,
        options=["not_checked", "checking", "success", "failed"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.check_status,
    ),
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
    async_add_entities(
        HistorySensor(entry.runtime_data.history, entry, key)
        for key in ("history_total", "history_latest", "history_status", "history_last_success")
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
    def available(self) -> bool:
        if self.entity_description.key in {"check_status", "last_success"}:
            return True
        return self.coordinator.data is not None and super().available

    @property
    def native_value(self) -> SensorValue:
        if (
            self.entity_description.key not in {"check_status", "last_success"}
            and self.coordinator.data is None
        ):
            return None
        return self.entity_description.value_fn(self.coordinator)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.key == "check_status":
            return {"last_error": self.coordinator.last_error}
        if self.entity_description.key != "document_count":
            return None
        data = self.coordinator.data
        if data is None:
            return None
        return {
            "date_from": data.date_from.isoformat(),
            "date_to": data.date_to.isoformat(),
            "account_count": data.account_count,
        }


class HistorySensor(CoordinatorEntity[HistoryCoordinator], SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: HistoryCoordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="TAURON eBOK",
            manufacturer="TAURON",
            entry_type=DeviceEntryType.SERVICE,
        )
        if key in {"history_total", "history_latest"}:
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = "kWh"
            # A revisable archive is not a cumulative meter. Do not mix its
            # snapshots into Energy or AMIplus long-term statistics.
        elif key == "history_last_success":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        else:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = [
                "not_configured",
                "not_checked",
                "checking",
                "cached",
                "success",
                "failed",
            ]
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        # Retain confirmed cached values during an outage; freshness is explicit.
        return (
            self.key in {"history_status", "history_last_success"}
            or self.coordinator.data is not None
        )

    @property
    def native_value(self):
        if self.key == "history_status":
            return self.coordinator.check_status
        if self.key == "history_last_success":
            return self.coordinator.last_success
        months = self.coordinator.months
        if not months:
            return None
        if self.key == "history_latest":
            return months[-1]["kwh"]
        if any(m["kwh"] is None for m in months):
            return None
        return sum(Decimal(str(m["kwh"])) for m in months)

    @property
    def extra_state_attributes(self) -> dict:
        c = self.coordinator
        result = {
            "source": "moj_tauron",
            "period_basis": "reading_date_month",
            "last_error": c.last_error,
            "last_success": c.last_success.isoformat() if c.last_success else None,
            "last_check_success": {"success": True, "failed": False}.get(c.check_status),
        }
        if self.key == "history_total":
            result["months"] = c.months
            result["month_count"] = len(c.months)
        elif self.key == "history_latest" and c.months:
            result.update(c.months[-1])
        return result
