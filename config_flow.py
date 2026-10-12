"""Config flow for Cora integration."""
from __future__ import annotations

import logging
import types
from types import MappingProxyType
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import (
    CONF_URL,
    CONF_USER,
    CONF_PASS,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_URL): str,
        vol.Optional(CONF_USER): str,
        vol.Optional(CONF_PASS): str,
        vol.Optional(CONF_NOTIFY_SERVICE): str,
    }
)

DEFAULT_OPTIONS = types.MappingProxyType(
    {
        CONF_URL: "",
        CONF_USER: "username",
        CONF_PASS: "password",
        CONF_NOTIFY_SERVICE: "",
    }
)


async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    """Validate the user input allows us to connect.

    Data has the keys from STEP_USER_DATA_SCHEMA with values provided by the user.
    """
        
    # TODO Really should attempt a connection. See https://github.com/home-assistant/example-custom-config/blob/master/custom_components/detailed_hello_world_push/config_flow.py
    
    return data


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Cora."""
    # TODO remove config flow and use options

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
    #     """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            input = await validate_input(self.hass, user_input)
            return self.async_create_entry(title="", data=user_input)
            
    #         # except error.APIConnectionError:
    #         #     errors["base"] = "cannot_connect"
    #         # except error.AuthenticationError:
    #         #     errors["base"] = "invalid_auth"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
    #         #     errors["base"] = "unknown"
        else:
            return self.async_create_entry(title="", data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    """Cora config flow options handler."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = cora_config_option_schema({**self.config_entry.data, **self.config_entry.options})
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
        )


def cora_config_option_schema(options: dict[str, Any]) -> dict:
    """Return a schema for Cora options."""
    if not options:
        options = DEFAULT_OPTIONS
    return {
        vol.Required(
            CONF_URL,
            description={"suggested_value": options.get(CONF_URL, "")},
            default="",
        ): str,
        vol.Optional(
            CONF_USER,
            description={"suggested_value": options.get(CONF_USER, "")},
            default="",
        ): str,
        vol.Optional(
            CONF_PASS,
            description={"suggested_value": options.get(CONF_PASS, "")},
            default="",
        ): str,
        vol.Optional(
            CONF_NOTIFY_SERVICE,
            description={"suggested_value": options.get(CONF_NOTIFY_SERVICE, "")},
            default="",
        ): str,
    }
