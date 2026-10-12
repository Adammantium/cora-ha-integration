"""The Cora integration."""

# This code is from nodered_conversation and then modified for my needs
# https://github.com/roblandry/nodered_conversation
from __future__ import annotations

from datetime import datetime
import logging

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_track_time_interval

from .auth import CoraAuth
from .const import (
    CONF_URL,
    CONF_USER,
    CONF_PASS,
    DOMAIN,
    TOKEN_REFRESH_INTERVAL,
)
from .agent import CoraAgent

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cora from a config entry."""

    _LOGGER.info(f"entry.data: {entry.data}")

    # Settings changed in the options flow override the initial setup values
    config = {**entry.data, **entry.options}

    auth = CoraAuth(config[CONF_URL], config.get(CONF_USER, ""), config.get(CONF_PASS, ""))
    entry.runtime_data = auth
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
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
