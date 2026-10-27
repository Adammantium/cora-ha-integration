"""Next alarm and next timer sensors for the Cora integration."""
from __future__ import annotations

from datetime import datetime, timedelta
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
from .timers import TimerTracker

# Native timers can be paused, extended or cancelled on the satellite without
# Cora noticing, so the next timer sensor polls them
SCAN_INTERVAL = timedelta(seconds=5)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the next alarm and next timer sensors."""
    async_add_entities([
        CoraNextAlarmSensor(entry, entry.runtime_data.alarms),
        CoraNextTimerSensor(entry, entry.runtime_data.timers),
    ])


def cora_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the device the Cora entities belong to."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Cora",
        manufacturer="CoraConnect",
        entry_type=DeviceEntryType.SERVICE,
    )


def device_name(hass: HomeAssistant, device_id: str | None) -> str | None:
    """Return the name of a device."""
    device = dr.async_get(hass).async_get(device_id) if device_id else None
    return (device.name_by_user or device.name) if device else None


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
        self._attr_device_info = cora_device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        """Return when the next alarm rings."""
        next_alarm = self.alarms.next_alarm
        return next_alarm[0] if next_alarm else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all alarms."""
        now = dt_util.now()
        alarms = []
        for alarm in sorted(self.alarms.alarms.values(), key=lambda a: (a.hour, a.minute)):
            next_time = alarm.next_time(now)
            alarms.append({
                "id": alarm.id,
                "time": alarm.time_str,
                "days": [WEEKDAYS[day] for day in alarm.days],
                "label": alarm.label,
                "device": device_name(self.hass, alarm.device_id),
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


class CoraNextTimerSensor(SensorEntity):
    """Shows when the next timer ends, all timers are in the attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "next_timer"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, entry: ConfigEntry, timers: TimerTracker) -> None:
        """Initialize the sensor."""
        self.timers = timers
        self._attr_unique_id = f"{entry.entry_id}_next_timer"
        self._attr_device_info = cora_device_info(entry)

    @property
    def native_value(self) -> datetime | None:
        """Return when the next timer ends."""
        next_timer = self.timers.next_timer
        return next_timer.ends_at if next_timer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return all timers."""
        return {
            "timers": [
                {
                    "id": timer.id,
                    "name": timer.name,
                    "device": device_name(self.hass, timer.device_id),
                    "source": timer.source,
                    "duration": timer.seconds,
                    "ends_at": timer.ends_at.isoformat() if timer.ends_at else None,
                    "paused": timer.ends_at is None,
                }
                for timer in self.timers.timers
            ]
        }

    async def async_added_to_hass(self) -> None:
        """Update the state right away when a phone timer is added or expires."""
        self.async_on_remove(self.timers.add_listener(self.async_write_ha_state))
