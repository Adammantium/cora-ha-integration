"""Alarms managed by Cora for satellites and speakers."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
import logging
import uuid

from homeassistant.components.assist_satellite import AssistSatelliteEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_SUPPORTED_FEATURES, STATE_UNAVAILABLE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    ALARM_DEFAULT_MESSAGE,
    ALARM_REPEAT_PAUSE,
    ALARM_RING_TIMEOUT,
    DEFAULT_ALARM_SOUND,
    DEFAULT_TIMER_SOUND,
    DOMAIN,
    EVENT_ALARM_RINGING,
    EVENT_ALARM_STOPPED,
    EVENT_TIMER_RINGING,
    EVENT_TIMER_STOPPED,
    SOUNDS,
    SOUNDS_URL_PATH,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

WEEKDAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@dataclass
class Alarm:
    """An alarm, days are weekdays with 0 = Monday, empty for a one-time alarm."""

    id: str
    hour: int
    minute: int
    days: list[int] = field(default_factory=list)
    label: str = ""
    device_id: str | None = None
    enabled: bool = True
    # When a one-time alarm rings, fixed when it is set so a missed one can be detected
    at: str | None = None
    # "alarm", or "timer" for finished timers that Cora rings, those are never stored
    kind: str = "alarm"

    @property
    def time_str(self) -> str:
        """Return the time as HH:MM."""
        return f"{self.hour:02d}:{self.minute:02d}"

    def next_time(self, now: datetime) -> datetime | None:
        """Return when the alarm rings next, None if it is a one-time alarm that already passed."""
        if self.at is not None:
            at = dt_util.parse_datetime(self.at)
            return at if at is not None and at > now else None

        now = dt_util.as_local(now)
        for offset in range(8):
            day = now.date() + timedelta(days=offset)
            if self.days and day.weekday() not in self.days:
                continue
            candidate = datetime.combine(day, time(self.hour, self.minute), tzinfo=now.tzinfo)
            if candidate > now:
                return candidate
        return None


def next_one_time(hour: int, minute: int, now: datetime) -> datetime:
    """Return the next local time at hour:minute."""
    now = dt_util.as_local(now)
    candidate = datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo)
    if candidate <= now:
        candidate = datetime.combine(now.date() + timedelta(days=1), time(hour, minute), tzinfo=now.tzinfo)
    return candidate


class AlarmManager:
    """Stores, schedules and rings the alarms of one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.entry = entry
        self.alarms: dict[str, Alarm] = {}
        self._store: Store[dict] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.alarms_{entry.entry_id}")
        self._scheduled: dict[str, CALLBACK_TYPE] = {}
        # alarm id -> (alarm, ringing task)
        self._ringing: dict[str, tuple[Alarm, asyncio.Task]] = {}
        self._listeners: list[Callable[[], None]] = []
        # Sound keys of SOUNDS, set by the sound select entities
        self.alarm_sound = DEFAULT_ALARM_SOUND
        self.timer_sound = DEFAULT_TIMER_SOUND

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener when the alarms change, returns a function to remove it."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    async def async_load(self) -> None:
        """Load the stored alarms and schedule them, missed one-time alarms are dropped."""
        data = await self._store.async_load() or {}
        now = dt_util.now()
        dropped = False
        for item in data.get("alarms", []):
            try:
                alarm = Alarm(**item)
            except TypeError:
                _LOGGER.warning(f"Ignoring invalid stored alarm: {item}")
                dropped = True
                continue
            if alarm.next_time(now) is None:
                _LOGGER.info(f"Removing missed one-time alarm {alarm.time_str} ({alarm.id})")
                dropped = True
                continue
            self.alarms[alarm.id] = alarm
            self._schedule(alarm)
        if dropped:
            await self._async_save()

    @callback
    def async_unload(self) -> None:
        """Cancel all scheduled callbacks and ringing alarms."""
        for unsub in self._scheduled.values():
            unsub()
        self._scheduled.clear()
        for _, task in list(self._ringing.values()):
            task.cancel()

    async def _async_save(self) -> None:
        await self._store.async_save({"alarms": [asdict(a) for a in self.alarms.values()]})

    @callback
    def _schedule(self, alarm: Alarm) -> None:
        """Schedule the next ring of an alarm."""
        if unsub := self._scheduled.pop(alarm.id, None):
            unsub()
        if not alarm.enabled:
            return
        when = alarm.next_time(dt_util.now())
        if when is None:
            return
        _LOGGER.debug(f"Alarm {alarm.id} scheduled for {when}")

        @callback
        def _fire(now: datetime) -> None:
            self._scheduled.pop(alarm.id, None)
            self.entry.async_create_background_task(
                self.hass, self._async_fire(alarm.id), f"cora_alarm_fire_{alarm.id}"
            )

        self._scheduled[alarm.id] = async_track_point_in_time(self.hass, _fire, when)

    async def _async_fire(self, alarm_id: str) -> None:
        """Ring an alarm, then delete it if it was a one-time alarm or schedule the next ring."""
        alarm = self.alarms.get(alarm_id)
        if alarm is None:
            return

        if alarm.days:
            self._schedule(alarm)
        else:
            self.alarms.pop(alarm_id)
            await self._async_save()
        self._notify()
        self._start_ringing(alarm)

    @callback
    def async_ring_timer(self, device_id: str | None, name: str | None) -> None:
        """Ring a finished timer on a satellite that has no timer sound of its own."""
        now = dt_util.now()
        self._start_ringing(Alarm(
            id=uuid.uuid4().hex,
            hour=now.hour,
            minute=now.minute,
            label=name or "",
            device_id=device_id,
            kind="timer",
        ))

    @callback
    def _start_ringing(self, alarm: Alarm) -> None:
        """Ring an alarm or a finished timer until stopped or timed out."""
        # A new alarm on the same device replaces the one that is still ringing
        for other, other_task in list(self._ringing.values()):
            if other.device_id == alarm.device_id:
                other_task.cancel()

        self.hass.bus.async_fire(
            EVENT_TIMER_RINGING if alarm.kind == "timer" else EVENT_ALARM_RINGING,
            self._event_data(alarm),
        )
        task = self.entry.async_create_background_task(
            self.hass, self._async_ring(alarm), f"cora_alarm_{alarm.id}"
        )
        # Background tasks start eagerly, without a speaker the task is already done
        if not task.done():
            self._ringing[alarm.id] = (alarm, task)
            self._notify()

    def _event_data(self, alarm: Alarm) -> dict:
        return {
            "alarm_id": alarm.id,
            "time": alarm.time_str,
            "label": alarm.label,
            "device_id": alarm.device_id,
            "area_id": self._area_id(alarm.device_id),
        }

    def _area_id(self, device_id: str | None) -> str | None:
        if not device_id:
            return None
        device = dr.async_get(self.hass).async_get(device_id)
        return device.area_id if device else None

    def _get_satellite(self, device_id: str | None) -> str | None:
        """Return the assist_satellite entity of the device, if it has one that can announce."""
        if not device_id:
            return None
        for entity in er.async_entries_for_device(er.async_get(self.hass), device_id):
            if entity.domain != "assist_satellite" or entity.disabled:
                continue
            state = self.hass.states.get(entity.entity_id)
            if (
                state is not None
                and state.state != STATE_UNAVAILABLE
                and state.attributes.get(ATTR_SUPPORTED_FEATURES, 0) & AssistSatelliteEntityFeature.ANNOUNCE
            ):
                return entity.entity_id
        return None

    def _sound_path(self, alarm: Alarm) -> str:
        """Return the URL path of the sound selected for alarms or timers."""
        sound = self.timer_sound if alarm.kind == "timer" else self.alarm_sound
        return f"{SOUNDS_URL_PATH}/{SOUNDS.get(sound, SOUNDS[DEFAULT_ALARM_SOUND])}"

    def _sound_url(self, alarm: Alarm) -> str | None:
        try:
            base = get_url(self.hass, prefer_external=False)
        except NoURLAvailableError:
            _LOGGER.warning("No Home Assistant URL configured, can not play the alarm sound")
            return None
        return f"{base}{self._sound_path(alarm)}"

    async def _async_ring(self, alarm: Alarm) -> None:
        """Ring on the satellite of the device or a media player until stopped or timed out."""
        # Imported here, actions imports this module through the runtime data
        from .actions import call_media_player, get_media_player  # pylint: disable=import-outside-toplevel

        _LOGGER.info(f"Alarm {alarm.time_str} ringing on device {alarm.device_id}")
        # A label is spoken once before the alarm sound starts
        speak_label = bool(alarm.label)
        try:
            async with asyncio.timeout(ALARM_RING_TIMEOUT.total_seconds()):
                while True:
                    if satellite := self._get_satellite(alarm.device_id):
                        if speak_label:
                            data = {"message": alarm.label}
                            speak_label = False
                        else:
                            # Home Assistant turns the relative path into a URL the satellite can fetch
                            data = {
                                "message": ALARM_DEFAULT_MESSAGE,
                                "media_id": self._sound_path(alarm),
                                "preannounce": False,
                            }
                        try:
                            await self.hass.services.async_call(
                                "assist_satellite",
                                "announce",
                                {"entity_id": satellite, **data},
                                blocking=True,
                            )
                        except Exception as e:  # pylint: disable=broad-except
                            _LOGGER.warning(f"Failed to announce the alarm on {satellite}: {e}")
                    elif (player := get_media_player(
                        self.hass, self.entry, alarm.device_id, MediaPlayerEntityFeature.PLAY_MEDIA
                    )) and (url := self._sound_url(alarm)):
                        await call_media_player(self.hass, player, "media_player", "play_media", {
                            "media_content_id": url,
                            "media_content_type": "music",
                            "announce": True,
                        })
                    else:
                        _LOGGER.warning(f"No satellite or media player found for device {alarm.device_id}, the alarm can not ring")
                        return
                    await asyncio.sleep(ALARM_REPEAT_PAUSE.total_seconds())
        except TimeoutError:
            _LOGGER.info(f"Alarm {alarm.time_str} stopped after {ALARM_RING_TIMEOUT}")
        finally:
            self._ringing.pop(alarm.id, None)
            self.hass.bus.async_fire(
                EVENT_TIMER_STOPPED if alarm.kind == "timer" else EVENT_ALARM_STOPPED,
                self._event_data(alarm),
            )
            self._notify()

    @callback
    def async_stop_ringing(self, device_id: str | None = None) -> bool:
        """Stop ringing alarms of the device and its room, or all of them without a device.

        Returns True if an alarm was ringing.
        """
        area_id = self._area_id(device_id)
        stopped = False
        for alarm, task in list(self._ringing.values()):
            if (
                device_id is None
                or alarm.device_id == device_id
                or (area_id is not None and self._area_id(alarm.device_id) == area_id)
            ):
                task.cancel()
                stopped = True
        return stopped

    async def async_add(
        self,
        hour: int,
        minute: int,
        days: list[int] | None = None,
        label: str = "",
        device_id: str | None = None,
    ) -> Alarm:
        """Add an alarm, without days it rings once at the next hour:minute."""
        days = sorted(set(days or []))
        alarm = Alarm(
            id=uuid.uuid4().hex,
            hour=hour,
            minute=minute,
            days=days,
            label=label,
            device_id=device_id,
            at=None if days else next_one_time(hour, minute, dt_util.now()).isoformat(),
        )
        self.alarms[alarm.id] = alarm
        self._schedule(alarm)
        await self._async_save()
        self._notify()
        _LOGGER.info(f"Alarm set: {alarm}")
        return alarm

    async def async_remove(self, device_id: str | None, hour: int | None = None, minute: int | None = None) -> int:
        """Remove the device's alarms at hour:minute, or its next alarm without a time.

        Returns the number of removed alarms.
        """
        alarms = [a for a in self.alarms.values() if a.device_id == device_id]
        if hour is not None:
            alarms = [a for a in alarms if a.hour == hour and a.minute == minute]
        else:
            now = dt_util.now()
            upcoming = [(when, a) for a in alarms if a.enabled and (when := a.next_time(now))]
            alarms = [min(upcoming, key=lambda item: item[0])[1]] if upcoming else []

        for alarm in alarms:
            self.alarms.pop(alarm.id, None)
            if unsub := self._scheduled.pop(alarm.id, None):
                unsub()
            _LOGGER.info(f"Alarm removed: {alarm}")
        if alarms:
            await self._async_save()
            self._notify()
        return len(alarms)

    @property
    def next_alarm(self) -> tuple[datetime, Alarm] | None:
        """Return the next enabled alarm and when it rings."""
        now = dt_util.now()
        upcoming = [(when, a) for a in self.alarms.values() if a.enabled and (when := a.next_time(now))]
        return min(upcoming, key=lambda item: item[0]) if upcoming else None

    @property
    def ringing(self) -> list[Alarm]:
        """Return the alarms that are ringing right now."""
        return [alarm for alarm, _ in self._ringing.values()]
