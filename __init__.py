"""The Cora integration."""

# This code is from nodered_conversation and then modified for my needs
# https://github.com/roblandry/nodered_conversation
from __future__ import annotations

import logging

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .auth import CoraAuth
from .const import (
    CONF_URL,
    CONF_USER,
    CONF_PASS,
    DOMAIN,
)
from .agent import CoraAgent

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cora from a config entry."""

    _LOGGER.info(f"entry.data: {entry.data}")

    # Settings changed in the options flow override the initial setup values
    config = {**entry.data, **entry.options}

    auth = CoraAuth(config[CONF_URL], config.get(CONF_USER, ""), config.get(CONF_PASS, ""))
    conversation.async_set_agent(hass, entry, CoraAgent(hass, entry, auth))

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its settings were changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    conversation.async_unset_agent(hass, entry)
    return True
