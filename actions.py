"""Actions that Cora can ask Home Assistant to perform."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
import re

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .const import (
    ACTION_OPEN_WEB,
    ACTION_PAUSE_MUSIC,
    ACTION_PLAY_MUSIC,
    ACTION_PLAY_MUSIC_TRACK,
    ACTION_REMOVE_TIMER,
    ACTION_SET_TIMER,
    ACTION_SHOW_TIMER,
    ACTION_STOP_MUSIC,
    CONF_DEFAULT_PLAYLIST,
    CONF_MEDIA_PLAYER,
    CONF_MUSIC_APP,
    CONF_NOTIFY_SERVICE,
    DEFAULT_MUSIC_APP,
)

_LOGGER = logging.getLogger(__name__)

MUSIC_ASSISTANT_DOMAIN = "music_assistant"

# Number of random tracks queued when nothing is playing and no playlist is set
RANDOM_TRACK_COUNT = 50

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


def get_device_notify_service(hass: HomeAssistant, device_id: str | None) -> str | None:
    """Return the mobile_app notify service if the request came from a phone."""
    if device_id:
        device = dr.async_get(hass).async_get(device_id)
        if device:
            for entry_id in device.config_entries:
                device_entry = hass.config_entries.async_get_entry(entry_id)
                if device_entry and device_entry.domain == "mobile_app" and "device_name" in device_entry.data:
                    service = f"mobile_app_{slugify(device_entry.data['device_name'])}"
                    if hass.services.has_service("notify", service):
                        return service
    return None


def get_fallback_notify_service(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the notify service configured for this integration."""
    service = {**entry.data, **entry.options}.get(CONF_NOTIFY_SERVICE, "").removeprefix("notify.")
    if service and hass.services.has_service("notify", service):
        return service
    return None


def get_notify_service(hass: HomeAssistant, entry: ConfigEntry, device_id: str | None) -> str | None:
    """Find the mobile_app notify service for the device that sent the request."""
    return get_device_notify_service(hass, device_id) or get_fallback_notify_service(hass, entry)


async def send_phone_command(hass: HomeAssistant, service: str, command: str, data: dict) -> None:
    """Send a notification command to the companion app."""
    _LOGGER.info(f"Sending {command} {data} via notify.{service}")
    # https://companion.home-assistant.io/docs/notifications/notification-commands/
    try:
        await hass.services.async_call(
            "notify",
            service,
            {"message": command, "data": data},
            blocking=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        _LOGGER.warning(f"Failed to send {command} {data}: {e}")


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

    await send_phone_command(hass, service, "command_activity", intent_data)


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


def get_media_player(hass: HomeAssistant, entry: ConfigEntry, device_id: str | None) -> str | None:
    """Find the media player for the device that sent the request.

    Prefers Music Assistant players on the device, then Music Assistant players
    in the same area, then any media player on the device and finally the
    media player configured for this integration.
    """
    if device_id:
        entity_reg = er.async_get(hass)
        device_reg = dr.async_get(hass)

        device_players = [
            e for e in er.async_entries_for_device(entity_reg, device_id)
            if e.domain == "media_player" and not e.disabled
        ]

        area_players: list[er.RegistryEntry] = []
        device = device_reg.async_get(device_id)
        if device and device.area_id:
            area_players = [
                e for e in er.async_entries_for_area(entity_reg, device.area_id)
                if e.domain == "media_player" and not e.disabled
            ]
            for area_device in dr.async_entries_for_area(device_reg, device.area_id):
                area_players.extend(
                    e for e in er.async_entries_for_device(entity_reg, area_device.id)
                    if e.domain == "media_player" and not e.disabled
                    # Entities with their own area belong to that area, not the device's
                    and (e.area_id is None or e.area_id == device.area_id)
                )

        candidates = (
            [e for e in device_players if e.platform == MUSIC_ASSISTANT_DOMAIN]
            + [e for e in area_players if e.platform == MUSIC_ASSISTANT_DOMAIN]
            + device_players
        )
        for candidate in candidates:
            if hass.states.get(candidate.entity_id) is not None:
                return candidate.entity_id

    # Fall back to the media player configured for this integration
    entity_id = {**entry.data, **entry.options}.get(CONF_MEDIA_PLAYER, "").strip()
    if entity_id and hass.states.get(entity_id) is not None:
        return entity_id
    return None


def get_music_target(
    hass: HomeAssistant, entry: ConfigEntry, device_id: str | None
) -> tuple[str, str] | None:
    """Decide where music actions go.

    Returns ("phone", notify service) or ("player", media player entity id).
    A phone that sent the request wins, then media players, then the fallback phone.
    """
    if service := get_device_notify_service(hass, device_id):
        return ("phone", service)
    if entity_id := get_media_player(hass, entry, device_id):
        return ("player", entity_id)
    if service := get_fallback_notify_service(hass, entry):
        return ("phone", service)
    return None


def get_music_app(entry: ConfigEntry) -> str:
    """Return the package name of the music app on the phone."""
    return {**entry.data, **entry.options}.get(CONF_MUSIC_APP, "").strip() or DEFAULT_MUSIC_APP


def clean_extra(value: str) -> str:
    """Remove characters that break the companion app intent_extras format."""
    return re.sub(r"[,:]", " ", value).strip()


async def call_media_player(hass: HomeAssistant, entity_id: str, domain: str, service: str, data: dict | None = None) -> None:
    """Call a service on a media player."""
    _LOGGER.info(f"Calling {domain}.{service} on {entity_id} with {data}")
    try:
        await hass.services.async_call(
            domain,
            service,
            {"entity_id": entity_id, **(data or {})},
            blocking=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        _LOGGER.warning(f"Failed to call {domain}.{service} on {entity_id}: {e}")


async def control_music(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    media_command: str,
) -> None:
    """Send play, pause or stop to the phone music app or the media player."""
    target = get_music_target(hass, entry, user_input.device_id)
    if target is None:
        _LOGGER.warning(f"No phone or media player found for device {user_input.device_id}, can not {media_command}")
        return

    kind, target_id = target
    if kind == "phone":
        # Needs notification access for the companion app
        # https://companion.home-assistant.io/docs/notifications/notification-commands/#media
        await send_phone_command(hass, target_id, "command_media", {
            "media_command": media_command,
            "media_package_name": get_music_app(entry),
        })
    else:
        await call_media_player(hass, target_id, "media_player", f"media_{media_command}")


def phone_music_is_active(hass: HomeAssistant, entry: ConfigEntry, device_id: str | None) -> bool:
    """Check the companion app media session sensor for a session of the music app.

    Returns True if the state is unknown, so we fall back to resuming.
    """
    if not device_id:
        return True

    entity_reg = er.async_get(hass)
    for e in er.async_entries_for_device(entity_reg, device_id):
        if e.domain == "sensor" and e.unique_id.endswith("media_session"):
            state = hass.states.get(e.entity_id)
            if state is None:
                return True
            _LOGGER.debug(f"Media session sensor {e.entity_id}: {state.state} {state.attributes}")
            playback_state = state.attributes.get(f"playback_state_{get_music_app(entry)}")
            return str(playback_state).lower() in ("playing", "paused", "buffering")

    _LOGGER.info("Enable the media session sensor in the companion app to detect if music is running")
    return True


def player_music_is_active(hass: HomeAssistant, entity_id: str) -> bool:
    """Return True if the media player has something to resume."""
    state = hass.states.get(entity_id)
    return state is not None and state.state in ("playing", "paused", "buffering")


async def play_random_tracks(hass: HomeAssistant, entity_id: str) -> None:
    """Queue random tracks from the Music Assistant library."""
    player = er.async_get(hass).async_get(entity_id)
    if player is None or player.config_entry_id is None:
        return

    # https://www.home-assistant.io/integrations/music_assistant/#action-music_assistantget_library
    try:
        response = await hass.services.async_call(
            MUSIC_ASSISTANT_DOMAIN,
            "get_library",
            {
                "config_entry_id": player.config_entry_id,
                "media_type": "track",
                "order_by": "random",
                "limit": RANDOM_TRACK_COUNT,
            },
            blocking=True,
            return_response=True,
        )
    except Exception as e:  # pylint: disable=broad-except
        _LOGGER.warning(f"Failed to get random tracks from Music Assistant: {e}")
        return

    uris = [item["uri"] for item in (response or {}).get("items", []) if item.get("uri")]
    if not uris:
        _LOGGER.warning("Music Assistant library returned no tracks")
        return

    await call_media_player(hass, entity_id, MUSIC_ASSISTANT_DOMAIN, "play_media", {
        "media_id": uris,
        "media_type": "track",
        "enqueue": "replace",
    })


async def play_music(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Play music, parameters are [playlist (optional)].

    With a playlist it is always started. Without one, paused music is resumed,
    otherwise the default playlist or random tracks are played.
    """
    playlist = str(parameters[0]).strip() if parameters else ""

    target = get_music_target(hass, entry, user_input.device_id)
    if target is None:
        _LOGGER.warning(f"No phone or media player found for device {user_input.device_id}, can not play music")
        return
    kind, target_id = target

    if not playlist:
        if kind == "phone":
            # Only a phone that sent the request has a known device for the media session sensor
            device_id = user_input.device_id if get_device_notify_service(hass, user_input.device_id) else None
            active = phone_music_is_active(hass, entry, device_id)
        else:
            active = player_music_is_active(hass, target_id)
        if active:
            await control_music(hass, entry, user_input, "play")
            return
        playlist = {**entry.data, **entry.options}.get(CONF_DEFAULT_PLAYLIST, "").strip()

    if kind == "phone":
        # https://developer.android.com/reference/android/provider/MediaStore#INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH
        if playlist:
            playlist = clean_extra(playlist)
            extras = [
                f"query:{playlist}",
                "android.intent.extra.focus:vnd.android.cursor.item/playlist",
                f"android.intent.extra.playlist:{playlist}",
            ]
        else:
            # Unstructured search without a query lets the app pick some music
            extras = ["android.intent.extra.focus:vnd.android.cursor.item/*"]
        await send_phone_command(hass, target_id, "command_activity", {
            "intent_action": "android.media.action.MEDIA_PLAY_FROM_SEARCH",
            "intent_package_name": get_music_app(entry),
            "intent_extras": ",".join(extras),
        })
        return

    if not hass.services.has_service(MUSIC_ASSISTANT_DOMAIN, "play_media"):
        # Without Music Assistant all we can do is resume
        _LOGGER.warning("Music Assistant is not available, can not start a playlist or random music")
        await control_music(hass, entry, user_input, "play")
        return

    if playlist:
        await call_media_player(hass, target_id, MUSIC_ASSISTANT_DOMAIN, "play_media", {
            "media_id": playlist,
            "media_type": "playlist",
            "enqueue": "replace",
        })
    else:
        await play_random_tracks(hass, target_id)


async def pause_music(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Pause playback."""
    await control_music(hass, entry, user_input, "pause")


async def stop_music(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Stop playback."""
    await control_music(hass, entry, user_input, "stop")


async def play_music_track(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Play a track, parameters are [track, artist (optional)]."""
    track = str(parameters[0]).strip() if parameters else ""
    if not track:
        _LOGGER.warning(f"Invalid PLAY_MUSIC_TRACK parameters: {parameters}")
        return
    artist = str(parameters[1]).strip() if len(parameters) > 1 else ""

    target = get_music_target(hass, entry, user_input.device_id)
    if target is None:
        _LOGGER.warning(f"No phone or media player found for device {user_input.device_id}, can not play track")
        return

    kind, target_id = target
    if kind == "phone":
        # https://developer.android.com/reference/android/provider/MediaStore#INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH
        track = clean_extra(track)
        artist = clean_extra(artist)
        extras = [
            f"query:{track} {artist}".strip(),
            "android.intent.extra.focus:vnd.android.cursor.item/audio",
            f"android.intent.extra.title:{track}",
        ]
        if artist:
            extras.append(f"android.intent.extra.artist:{artist}")
        await send_phone_command(hass, target_id, "command_activity", {
            "intent_action": "android.media.action.MEDIA_PLAY_FROM_SEARCH",
            "intent_package_name": get_music_app(entry),
            "intent_extras": ",".join(extras),
        })
        return

    if not hass.services.has_service(MUSIC_ASSISTANT_DOMAIN, "play_media"):
        _LOGGER.warning("Music Assistant is not available, can not play track")
        return

    data = {"media_id": track, "media_type": "track", "enqueue": "play"}
    if artist:
        data["artist"] = artist

    # https://www.home-assistant.io/integrations/music_assistant/#action-music_assistantplay_media
    await call_media_player(hass, target_id, MUSIC_ASSISTANT_DOMAIN, "play_media", data)


ACTIONS: dict[str, ActionHandler] = {
    ACTION_SET_TIMER: set_timer,
    ACTION_SHOW_TIMER: show_timer,
    ACTION_REMOVE_TIMER: remove_timer,
    ACTION_OPEN_WEB: open_web,
    ACTION_PLAY_MUSIC: play_music,
    ACTION_PAUSE_MUSIC: pause_music,
    ACTION_STOP_MUSIC: stop_music,
    ACTION_PLAY_MUSIC_TRACK: play_music_track,
}
