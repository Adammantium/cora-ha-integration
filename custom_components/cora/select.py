"""Sound selection for alarms and timers."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .alarms import AlarmManager
from .const import SOUNDS
from .sensor import cora_device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sound selects."""
    alarms = entry.runtime_data.alarms
    async_add_entities([
        CoraSoundSelect(entry, alarms, "alarm_sound"),
        CoraSoundSelect(entry, alarms, "timer_sound"),
    ])


class CoraSoundSelect(SelectEntity, RestoreEntity):
    """Selects the sound for alarms or finished timers, kept across restarts."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(SOUNDS)
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, alarms: AlarmManager, key: str) -> None:
        """Initialize the select, key is the AlarmManager attribute it sets."""
        self.alarms = alarms
        self.key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = cora_device_info(entry)

    @property
    def current_option(self) -> str:
        """Return the selected sound."""
        return getattr(self.alarms, self.key)

    async def async_select_option(self, option: str) -> None:
        """Select a sound."""
        setattr(self.alarms, self.key, option)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Restore the sound selected before the restart."""
        if (state := await self.async_get_last_state()) and state.state in SOUNDS:
            setattr(self.alarms, self.key, state.state)
