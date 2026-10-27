"""Next alarm sensor for the Cora integration."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .alarms import WEEKDAYS, AlarmManager
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the next alarm sensor."""
    async_add_entities([CoraNextAlarmSensor(entry, entry.runtime_data.alarms)])


class CoraNextAlarmSensor(SensorEntity):
    """Shows when the next alarm managed by Cora rings, all alarms are in the attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_alarm"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, alarms: AlarmManager) -> None:
        """Initialize the sensor."""
        self.alarms = alarms
        self._attr_unique_id = f"{entry.entry_id}_next_alarm"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Cora",
            manufacturer="CoraConnect",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def native_value(self) -> datetime | None:
        """Return when the next alarm rings."""
        next_alarm = self.alarms.next_alarm
        return next_alarm[0] if next_alarm else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all alarms."""
        device_reg = dr.async_get(self.hass)
        now = dt_util.now()

        def device_name(device_id: str | None) -> str | None:
            device = device_reg.async_get(device_id) if device_id else None
            return (device.name_by_user or device.name) if device else None

        alarms = []
        for alarm in sorted(self.alarms.alarms.values(), key=lambda a: (a.hour, a.minute)):
            next_time = alarm.next_time(now)
            alarms.append({
                "id": alarm.id,
                "time": alarm.time_str,
                "days": [WEEKDAYS[day] for day in alarm.days],
                "label": alarm.label,
                "device": device_name(alarm.device_id),
                "enabled": alarm.enabled,
                "next": next_time.isoformat() if next_time else None,
            })
        return {
            "alarms": alarms,
            "ringing": [alarm.id for alarm in self.alarms.ringing],
        }

    async def async_added_to_hass(self) -> None:
        """Update the state whenever the alarms change."""
        self.async_on_remove(self.alarms.add_listener(self.async_write_ha_state))
