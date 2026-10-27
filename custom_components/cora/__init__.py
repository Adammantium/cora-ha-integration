"""The Cora integration."""

# This code is from nodered_conversation and then modified for my needs
# https://github.com/roblandry/nodered_conversation
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path

from homeassistant.components import conversation
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .alarms import AlarmManager
from .auth import CoraAuth
from .timers import TimerTracker
from .const import (
    CONF_URL,
    CONF_USER,
    CONF_PASS,
    DOMAIN,
    SOUNDS_URL_PATH,
    TOKEN_REFRESH_INTERVAL,
)
from .agent import CoraAgent

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SELECT, Platform.SENSOR]


@dataclass
class CoraData:
    """Runtime data of a config entry."""

    auth: CoraAuth
    alarms: AlarmManager
    timers: TimerTracker


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the bundled sounds, media players fetch the alarm sound from there."""
    await hass.http.async_register_static_paths([
        StaticPathConfig(SOUNDS_URL_PATH, str(Path(__file__).parent / "sounds"), False)
    ])
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cora from a config entry."""

    _LOGGER.info(f"entry.data: {entry.data}")

    # Settings changed in the options flow override the initial setup values
    config = {**entry.data, **entry.options}

    auth = CoraAuth(config[CONF_URL], config.get(CONF_USER, ""), config.get(CONF_PASS, ""))
    alarms = AlarmManager(hass, entry)
    await alarms.async_load()
    entry.runtime_data = CoraData(auth, alarms, TimerTracker(hass, alarms.async_ring_timer))
    conversation.async_set_agent(hass, entry, CoraAgent(hass, entry, auth))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Log in right away and then refresh the token periodically to keep it alive
    entry.async_create_background_task(hass, auth.get_token(), "cora_login")

    async def async_refresh_token(now: datetime) -> None:
        await auth.refresh_token()

    entry.async_on_unload(async_track_time_interval(hass, async_refresh_token, TOKEN_REFRESH_INTERVAL))
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its settings were changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    conversation.async_unset_agent(hass, entry)
    entry.runtime_data.alarms.async_unload()
    entry.runtime_data.timers.async_unload()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
