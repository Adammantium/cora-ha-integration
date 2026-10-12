"""Actions that Cora can ask Home Assistant to perform."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.util import slugify

from .const import (
    ACTION_OPEN_WEB,
    ACTION_REMOVE_TIMER,
    ACTION_SET_TIMER,
    ACTION_SHOW_TIMER,
    CONF_NOTIFY_SERVICE,
)

_LOGGER = logging.getLogger(__name__)

ActionHandler = Callable[
    [HomeAssistant, ConfigEntry, conversation.ConversationInput, list],
    Awaitable[None],
]


async def async_handle_action(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    result: dict,
) -> None:
    """Run the action from an endpoint response, if there is one."""
    action = str(result.get("action") or "").upper()
    if not action:
        return

    handler = ACTIONS.get(action)
    if handler is None:
        _LOGGER.warning(f"Unknown action: {action}")
        return

    await handler(hass, entry, user_input, result.get("actionParameters") or [])


def get_notify_service(hass: HomeAssistant, entry: ConfigEntry, device_id: str | None) -> str | None:
    """Find the mobile_app notify service for the device that sent the request."""
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device:
            for entry_id in device.config_entries:
                device_entry = hass.config_entries.async_get_entry(entry_id)
                if device_entry and device_entry.domain == "mobile_app" and "device_name" in device_entry.data:
                    service = f"mobile_app_{slugify(device_entry.data['device_name'])}"
                    if hass.services.has_service("notify", service):
                        return service

    # Fall back to the notify service configured for this integration
    service = {**entry.data, **entry.options}.get(CONF_NOTIFY_SERVICE, "").removeprefix("notify.")
    if service and hass.services.has_service("notify", service):
        return service
    return None


async def start_activity(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    intent_data: dict,
) -> None:
    """Tell the companion app on the phone to start an Android activity."""
    service = get_notify_service(hass, entry, user_input.device_id)
    if service is None:
        _LOGGER.warning(f"No mobile_app notify service found for device {user_input.device_id}, can not start {intent_data}")
        return

    _LOGGER.info(f"Starting activity {intent_data} via notify.{service}")
    # https://companion.home-assistant.io/docs/notifications/notification-commands/#activity
    try:
        await hass.services.async_call(
            "notify",
            service,
            {"message": "command_activity", "data": intent_data},
            blocking=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        _LOGGER.warning(f"Failed to start activity {intent_data}: {e}")


async def set_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Start a system timer, parameters[0] is the length in seconds."""
    try:
        seconds = int(parameters[0])
    except (IndexError, ValueError, TypeError):
        _LOGGER.warning(f"Invalid SET_TIMER parameters: {parameters}")
        return

    await start_activity(hass, entry, user_input, {
        "intent_action": "android.intent.action.SET_TIMER",
        "intent_extras": f"android.intent.extra.alarm.LENGTH:{seconds},android.intent.extra.alarm.SKIP_UI:true",
    })


async def show_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Open the clock app showing all timers."""
    await start_activity(hass, entry, user_input, {
        "intent_action": "android.intent.action.SHOW_TIMERS",
    })


async def remove_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Dismiss the timer that is currently ringing.

    Android has no public intent to delete a running timer, DISMISS_TIMER
    only dismisses expired timers.
    """
    await start_activity(hass, entry, user_input, {
        "intent_action": "android.intent.action.DISMISS_TIMER",
    })


async def open_web(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Open a URL in the browser, parameters[0] is the URL."""
    url = str(parameters[0]).strip() if parameters else ""
    if not url.lower().startswith(("http://", "https://")):
        _LOGGER.warning(f"Invalid OPEN_WEB parameters: {parameters}")
        return

    await start_activity(hass, entry, user_input, {
        "intent_action": "android.intent.action.VIEW",
        "intent_uri": url,
    })


ACTIONS: dict[str, ActionHandler] = {
    ACTION_SET_TIMER: set_timer,
    ACTION_SHOW_TIMER: show_timer,
    ACTION_REMOVE_TIMER: remove_timer,
    ACTION_OPEN_WEB: open_web,
}
