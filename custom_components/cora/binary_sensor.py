"""Connection status sensor for the Cora integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .auth import CoraAuth
from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the connection sensor."""
    async_add_entities([CoraConnectionSensor(entry, entry.runtime_data.auth)])


class CoraConnectionSensor(BinarySensorEntity):
    """Shows whether the Cora endpoint is reachable and the login works."""

    _attr_has_entity_name = True
    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, auth: CoraAuth) -> None:
        """Initialize the sensor."""
        self.auth = auth
        self._attr_unique_id = f"{entry.entry_id}_connection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Cora",
            manufacturer="CoraConnect",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if connected."""
        return self.auth.connected

    async def async_added_to_hass(self) -> None:
        """Update the state whenever the connection state changes."""
        self.async_on_remove(self.auth.add_listener(self.async_write_ha_state))
