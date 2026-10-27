"""Actions that Cora can ask Home Assistant to perform."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from urllib.parse import quote

from homeassistant.components import conversation
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import slugify

from .const import (
    ACTION_DECREASE_VOLUME,
    ACTION_INCREASE_VOLUME,
    ACTION_NEXT_MUSIC_TRACK,
    ACTION_OPEN_WEB,
    ACTION_PAUSE_MUSIC,
    ACTION_PLAY_MUSIC,
    ACTION_PLAY_MUSIC_TRACK,
    ACTION_PREVIOUS_MUSIC_TRACK,
    ACTION_REMOVE_TIMER,
    ACTION_SET_TIMER,
    ACTION_SET_VOLUME,
    ACTION_SHOW_TIMER,
    ACTION_STOP,
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

# Volume change in percent for INCREASE_VOLUME and DECREASE_VOLUME
DEFAULT_VOLUME_STEP = 10

# Music stream maximum on stock Android, used if the volume sensor is not enabled
DEFAULT_PHONE_MAX_VOLUME = 15

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


def player_can_play(
    hass: HomeAssistant,
    entity_id: str,
    features: MediaPlayerEntityFeature,
    music_assistant: bool,
) -> bool:
    """Check if a media player is available and supports what the action needs."""
    state = hass.states.get(entity_id)
    if state is None or state.state == STATE_UNAVAILABLE:
        return False
    if music_assistant:
        entity = er.async_get(hass).async_get(entity_id)
        if entity is None or entity.platform != MUSIC_ASSISTANT_DOMAIN:
            return False
    return state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) & features == features


def get_nearby_players(
    hass: HomeAssistant, device_id: str
) -> tuple[list[er.RegistryEntry], list[er.RegistryEntry]]:
    """Return the media players on the device and the ones in the device's room."""
    entity_reg = er.async_get(hass)
    device_reg = dr.async_get(hass)

    device_players = [
        e for e in er.async_entries_for_device(entity_reg, device_id)
        if e.domain == "media_player" and not e.disabled
    ]

    room_players: list[er.RegistryEntry] = []
    device = device_reg.async_get(device_id)
    if device and device.area_id:
        room_players = [
            e for e in er.async_entries_for_area(entity_reg, device.area_id)
            if e.domain == "media_player" and not e.disabled and e.device_id != device_id
        ]
        for room_device in dr.async_entries_for_area(device_reg, device.area_id):
            if room_device.id == device_id:
                continue
            room_players.extend(
                e for e in er.async_entries_for_device(entity_reg, room_device.id)
                if e.domain == "media_player" and not e.disabled
                # Entities with their own area belong to that area, not the device's
                and (e.area_id is None or e.area_id == device.area_id)
            )

    return device_players, room_players


def get_media_player(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device_id: str | None,
    features: MediaPlayerEntityFeature,
    music_assistant: bool = False,
) -> str | None:
    """Find a media player for the device that sent the request.

    First players on the device itself, then speakers in the same room and
    finally the media player configured for this integration. Within the device
    and the room, Music Assistant players and players that are already playing win.
    """
    if device_id:
        device_players, room_players = get_nearby_players(hass, device_id)

        def rank(entity: er.RegistryEntry) -> tuple[bool, bool]:
            state = hass.states.get(entity.entity_id)
            playing = state is not None and state.state in ("playing", "paused", "buffering")
            return (entity.platform != MUSIC_ASSISTANT_DOMAIN, not playing)

        for players in (device_players, room_players):
            for candidate in sorted(players, key=rank):
                if player_can_play(hass, candidate.entity_id, features, music_assistant):
                    return candidate.entity_id

    # Fall back to the media player configured for this integration
    entity_id = {**entry.data, **entry.options}.get(CONF_MEDIA_PLAYER, "").strip()
    if entity_id and player_can_play(hass, entity_id, features, music_assistant):
        return entity_id
    return None


def get_music_target(
    hass: HomeAssistant,
    entry: ConfigEntry,
    device_id: str | None,
    features: MediaPlayerEntityFeature = MediaPlayerEntityFeature(0),
    music_assistant: bool = False,
) -> tuple[str, str] | None:
    """Decide where music actions go.

    Returns ("phone", notify service) or ("player", media player entity id).
    A phone that sent the request wins, then media players, then the fallback phone.
    """
    if service := get_device_notify_service(hass, device_id):
        return ("phone", service)
    if entity_id := get_media_player(hass, entry, device_id, features, music_assistant):
        return ("player", entity_id)
    if service := get_fallback_notify_service(hass, entry):
        return ("phone", service)
    return None


def get_music_app(entry: ConfigEntry) -> str:
    """Return the package name of the music app on the phone."""
    return {**entry.data, **entry.options}.get(CONF_MUSIC_APP, "").strip() or DEFAULT_MUSIC_APP


def string_extra(name: str, value: str) -> str:
    """Format a string intent extra, urlencoded so commas and colons do not break the format."""
    # https://companion.home-assistant.io/docs/notifications/notification-commands/#broadcast-intent
    return f"{name}:{quote(value, safe='')}:String.urlencoded"


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


MEDIA_COMMAND_FEATURES = {
    "play": MediaPlayerEntityFeature.PLAY,
    "pause": MediaPlayerEntityFeature.PAUSE,
    "stop": MediaPlayerEntityFeature.STOP,
    "next": MediaPlayerEntityFeature.NEXT_TRACK,
    "previous": MediaPlayerEntityFeature.PREVIOUS_TRACK,
}

# media_player services for the companion app media commands
MEDIA_COMMAND_SERVICES = {
    "play": "media_play",
    "pause": "media_pause",
    "stop": "media_stop",
    "next": "media_next_track",
    "previous": "media_previous_track",
}


async def send_music_command(hass: HomeAssistant, entry: ConfigEntry, kind: str, target_id: str, media_command: str) -> None:
    """Send play, pause or stop to the phone music app or a media player."""
    if kind == "phone":
        # Needs notification access for the companion app
        # https://companion.home-assistant.io/docs/notifications/notification-commands/#media
        await send_phone_command(hass, target_id, "command_media", {
            "media_command": media_command,
            "media_package_name": get_music_app(entry),
        })
    else:
        await call_media_player(hass, target_id, "media_player", MEDIA_COMMAND_SERVICES[media_command])


async def control_music(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    media_command: str,
) -> None:
    """Send play, pause or stop to the phone music app or the media player."""
    target = get_music_target(hass, entry, user_input.device_id, MEDIA_COMMAND_FEATURES[media_command])
    if target is None:
        _LOGGER.warning(f"No phone or media player found for device {user_input.device_id}, can not {media_command}")
        return

    await send_music_command(hass, entry, *target, media_command)


def is_phone_sensor(entity: er.RegistryEntry, key: str) -> bool:
    """Check if a registry entry is the companion app sensor with this key, e.g. sensor.<phone>_volume_level_music."""
    return entity.domain == "sensor" and (
        entity.unique_id.endswith(key) or entity.entity_id.endswith(f"_{key}")
    )


def phone_music_is_active(hass: HomeAssistant, entry: ConfigEntry, device_id: str | None) -> bool:
    """Check the companion app media session sensor for a session of the music app.

    Returns True if the state is unknown, so we fall back to resuming.
    """
    if not device_id:
        return True

    entity_reg = er.async_get(hass)
    for e in er.async_entries_for_device(entity_reg, device_id):
        if is_phone_sensor(e, "media_session"):
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

    resume_target = None
    if not playlist:
        resume_target = get_music_target(hass, entry, user_input.device_id, MediaPlayerEntityFeature.PLAY)
        if resume_target is not None:
            kind, target_id = resume_target
            if kind == "phone":
                # Only a phone that sent the request has a known device for the media session sensor
                device_id = user_input.device_id if get_device_notify_service(hass, user_input.device_id) else None
                active = phone_music_is_active(hass, entry, device_id)
            else:
                active = player_music_is_active(hass, target_id)
            if active:
                await send_music_command(hass, entry, kind, target_id, "play")
                return
        playlist = {**entry.data, **entry.options}.get(CONF_DEFAULT_PLAYLIST, "").strip()

    # Starting new music needs the phone app or a Music Assistant player
    target = get_music_target(hass, entry, user_input.device_id, MediaPlayerEntityFeature(0), music_assistant=True)
    if target is None:
        if resume_target is not None:
            # Without Music Assistant all we can do is resume
            _LOGGER.warning("No Music Assistant player found, can not start a playlist or random music")
            await send_music_command(hass, entry, *resume_target, "play")
        else:
            _LOGGER.warning(f"No phone or media player found for device {user_input.device_id}, can not play music")
        return
    kind, target_id = target

    if kind == "phone":
        # https://developer.android.com/reference/android/provider/MediaStore#INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH
        if playlist:
            extras = [
                string_extra("query", playlist),
                "android.intent.extra.focus:vnd.android.cursor.item/playlist",
                string_extra("android.intent.extra.playlist", playlist),
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


async def next_music_track(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Skip to the next track."""
    await control_music(hass, entry, user_input, "next")


async def previous_music_track(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Go back to the previous track."""
    await control_music(hass, entry, user_input, "previous")


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

    target = get_music_target(hass, entry, user_input.device_id, MediaPlayerEntityFeature(0), music_assistant=True)
    if target is None:
        _LOGGER.warning(f"No phone or Music Assistant player found for device {user_input.device_id}, can not play track")
        return

    kind, target_id = target
    if kind == "phone":
        # https://developer.android.com/reference/android/provider/MediaStore#INTENT_ACTION_MEDIA_PLAY_FROM_SEARCH
        extras = [
            string_extra("query", f"{track} {artist}".strip()),
            "android.intent.extra.focus:vnd.android.cursor.item/audio",
            string_extra("android.intent.extra.title", track),
        ]
        if artist:
            extras.append(string_extra("android.intent.extra.artist", artist))
        await send_phone_command(hass, target_id, "command_activity", {
            "intent_action": "android.media.action.MEDIA_PLAY_FROM_SEARCH",
            "intent_package_name": get_music_app(entry),
            "intent_extras": ",".join(extras),
        })
        return

    data = {"media_id": track, "media_type": "track", "enqueue": "play"}
    if artist:
        data["artist"] = artist

    # https://www.home-assistant.io/integrations/music_assistant/#action-music_assistantplay_media
    await call_media_player(hass, target_id, MUSIC_ASSISTANT_DOMAIN, "play_media", data)


def get_phone_volume(hass: HomeAssistant, device_id: str | None) -> tuple[int, int, int] | None:
    """Return (current, min, max) from the companion app music volume sensor."""
    if not device_id:
        return None

    for e in er.async_entries_for_device(er.async_get(hass), device_id):
        if is_phone_sensor(e, "volume_level_music"):
            state = hass.states.get(e.entity_id)
            if state is None:
                return None
            try:
                return (
                    int(float(state.state)),
                    int(state.attributes.get("min", 0)),
                    int(state.attributes["max"]),
                )
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug(f"Can not read volume sensor {e.entity_id}: {state.state} {state.attributes}")
                return None
    return None


async def change_volume(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    percent: int | None = None,
    step: int = 0,
) -> None:
    """Set the volume to percent, or change it by step percent."""
    target = get_music_target(hass, entry, user_input.device_id, MediaPlayerEntityFeature.VOLUME_SET)
    if target is None:
        _LOGGER.warning(f"No phone or media player found for device {user_input.device_id}, can not change volume")
        return

    kind, target_id = target
    if kind == "phone":
        device_id = user_input.device_id if get_device_notify_service(hass, user_input.device_id) else None
        volume = get_phone_volume(hass, device_id)
        if volume is None:
            if percent is None:
                _LOGGER.warning("Enable the music volume sensor in the companion app to change the volume relatively")
                return
            _LOGGER.info(f"Music volume sensor not found, assuming a maximum of {DEFAULT_PHONE_MAX_VOLUME}")
            current, minimum, maximum = 0, 0, DEFAULT_PHONE_MAX_VOLUME
        else:
            current, minimum, maximum = volume

        if percent is None:
            # Move at least one step so small volume ranges still change
            delta = round(step / 100 * (maximum - minimum)) or (1 if step > 0 else -1)
            level = current + delta
        else:
            level = minimum + round(percent / 100 * (maximum - minimum))
        level = max(minimum, min(maximum, level))

        # https://companion.home-assistant.io/docs/notifications/notification-commands/#volume-level
        await send_phone_command(hass, target_id, "command_volume_level", {
            "media_stream": "music_stream",
            "command": level,
        })
        return

    if percent is None:
        state = hass.states.get(target_id)
        current = state.attributes.get("volume_level") if state else None
        if current is None:
            await call_media_player(hass, target_id, "media_player", "volume_up" if step > 0 else "volume_down")
            return
        level = current + step / 100
    else:
        level = percent / 100
    await call_media_player(hass, target_id, "media_player", "volume_set", {
        "volume_level": round(max(0.0, min(1.0, level)), 2),
    })


def get_volume_step(parameters: list) -> int:
    """Return the volume step in percent, parameters are [step (optional)]."""
    try:
        return abs(int(float(parameters[0])))
    except (IndexError, ValueError, TypeError):
        return DEFAULT_VOLUME_STEP


async def increase_volume(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Turn the volume up."""
    await change_volume(hass, entry, user_input, step=get_volume_step(parameters))


async def decrease_volume(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Turn the volume down."""
    await change_volume(hass, entry, user_input, step=-get_volume_step(parameters))


async def set_volume(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Set the volume, parameters[0] is the volume between 0 and 100."""
    try:
        percent = max(0, min(100, round(float(parameters[0]))))
    except (IndexError, ValueError, TypeError):
        _LOGGER.warning(f"Invalid SET_VOLUME parameters: {parameters}")
        return
    await change_volume(hass, entry, user_input, percent=percent)


async def stop(
    hass: HomeAssistant,
    entry: ConfigEntry,
    user_input: conversation.ConversationInput,
    parameters: list,
) -> None:
    """Stop whatever is going on: expired timers and running music.

    Ringing alarms are left alone, DISMISS_ALARM would skip the next alarm
    if none is ringing and the companion app can not tell if one is.
    """
    # Expired timers on the phone, DISMISS_TIMER without a timer only affects expired ones
    if get_notify_service(hass, entry, user_input.device_id):
        await start_activity(hass, entry, user_input, {
            "intent_action": "android.intent.action.DISMISS_TIMER",
        })

    # Music on the phone that sent the request, a stop without a session does nothing
    if service := get_device_notify_service(hass, user_input.device_id):
        await send_music_command(hass, entry, "phone", service, "stop")

    # Music on the device, in its room or on the fallback player
    players: list[str] = []
    if user_input.device_id:
        device_players, room_players = get_nearby_players(hass, user_input.device_id)
        players = [e.entity_id for e in device_players + room_players]
    fallback_player = {**entry.data, **entry.options}.get(CONF_MEDIA_PLAYER, "").strip()
    if fallback_player and fallback_player not in players:
        players.append(fallback_player)

    for entity_id in players:
        state = hass.states.get(entity_id)
        if state is not None and state.state in ("playing", "buffering") and player_can_play(
            hass, entity_id, MediaPlayerEntityFeature.STOP, music_assistant=False
        ):
            await call_media_player(hass, entity_id, "media_player", "media_stop")


ACTIONS: dict[str, ActionHandler] = {
    ACTION_SET_TIMER: set_timer,
    ACTION_SHOW_TIMER: show_timer,
    ACTION_REMOVE_TIMER: remove_timer,
    ACTION_OPEN_WEB: open_web,
    ACTION_PLAY_MUSIC: play_music,
    ACTION_PAUSE_MUSIC: pause_music,
    ACTION_STOP_MUSIC: stop_music,
    ACTION_STOP: stop,
    ACTION_NEXT_MUSIC_TRACK: next_music_track,
    ACTION_PREVIOUS_MUSIC_TRACK: previous_music_track,
    ACTION_PLAY_MUSIC_TRACK: play_music_track,
    ACTION_INCREASE_VOLUME: increase_volume,
    ACTION_DECREASE_VOLUME: decrease_volume,
    ACTION_SET_VOLUME: set_volume,
}
