"""Overview of the running timers, native Home Assistant timers and timers on phones."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import time
from typing import Any
import uuid

from homeassistant.components.intent.const import TIMER_DATA
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time, async_track_time_interval
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# How often watched native timers are checked to notice when they finish
WATCH_INTERVAL = timedelta(seconds=1)

# A watched timer that disappears with at most this many seconds left has finished,
# with more left it was cancelled
FINISHED_MARGIN = 2.5


@dataclass
class TimerOverview:
    """A running or paused timer."""

    id: str
    device_id: str | None
    seconds: int
    # None while a native timer is paused
    ends_at: datetime | None
    name: str | None
    # "satellite" for native Home Assistant timers, "phone" for timers in the clock app
    source: str


class TimerTracker:
    """Keeps track of the timers set on phones and reads the native timers.

    The clock app on a phone does not report its timers, so they are remembered
    here when Cora starts them and forgotten when they expire. They are not
    stored, after a restart only native timers are known.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        on_finished: Callable[[str | None, str | None], None],
    ) -> None:
        """Initialize the tracker, on_finished(device_id, name) rings watched timers that finished."""
        self.hass = hass
        self._on_finished = on_finished
        self._phone_timers: dict[str, TimerOverview] = {}
        self._expire: dict[str, CALLBACK_TYPE] = {}
        self._listeners: list[Callable[[], None]] = []
        # Native timer id -> (timer, seconds left and active at the last check)
        self._watched: dict[str, tuple[Any, float, bool]] = {}
        self._watch_unsub: CALLBACK_TYPE | None = None

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener when the phone timers change, returns a function to remove it."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in self._listeners:
            listener()

    @callback
    def async_add_phone_timer(self, device_id: str | None, seconds: int) -> None:
        """Remember a timer started in the clock app of a phone."""
        timer = TimerOverview(
            id=uuid.uuid4().hex,
            device_id=device_id,
            seconds=seconds,
            ends_at=dt_util.utcnow() + timedelta(seconds=seconds),
            name=None,
            source="phone",
        )
        self._phone_timers[timer.id] = timer

        @callback
        def _expired(now: datetime) -> None:
            self._expire.pop(timer.id, None)
            self._phone_timers.pop(timer.id, None)
            self._notify()

        self._expire[timer.id] = async_track_point_in_time(self.hass, _expired, timer.ends_at)
        self._notify()

    @callback
    def async_watch_native_timer(self, device_id: str) -> None:
        """Watch the newest native timer of a device and ring it with Cora when it finishes.

        For satellites without a timer sound of their own. The timer manager only
        tells the device about finished timers, so the timer is checked every second.
        """
        timer_manager = self.hass.data.get(TIMER_DATA)
        if timer_manager is None:
            return
        candidates = [
            t for t in timer_manager.timers.values()
            if t.device_id == device_id and t.id not in self._watched and not t.conversation_command
        ]
        if not candidates:
            return
        timer = max(candidates, key=lambda t: t.created_at)
        self._watched[timer.id] = (timer, timer.seconds_left, timer.is_active)
        _LOGGER.debug(f"Watching native timer {timer.id} on device {device_id}")
        if self._watch_unsub is None:
            self._watch_unsub = async_track_time_interval(self.hass, self._check_watched, WATCH_INTERVAL)

    @callback
    def _check_watched(self, now: datetime) -> None:
        timer_manager = self.hass.data.get(TIMER_DATA)
        for timer_id, (timer, seconds_left, active) in list(self._watched.items()):
            if timer_manager is not None and timer_id in timer_manager.timers:
                self._watched[timer_id] = (timer, timer.seconds_left, timer.is_active)
                continue

            # Finished and cancelled timers are both removed, only a running timer
            # that was about to end has finished
            self._watched.pop(timer_id)
            if active and seconds_left <= FINISHED_MARGIN:
                _LOGGER.info(f"Native timer {timer_id} finished on device {timer.device_id}")
                self._on_finished(timer.device_id, timer.name)
            else:
                _LOGGER.debug(f"Native timer {timer_id} was cancelled")

        if not self._watched and self._watch_unsub is not None:
            self._watch_unsub()
            self._watch_unsub = None

    @callback
    def async_unload(self) -> None:
        """Cancel the expiry callbacks and stop watching native timers."""
        for unsub in self._expire.values():
            unsub()
        self._expire.clear()
        self._watched.clear()
        if self._watch_unsub is not None:
            self._watch_unsub()
            self._watch_unsub = None

    def _native_timers(self) -> list[TimerOverview]:
        """Read the timers of Home Assistant's timer manager."""
        timer_manager = self.hass.data.get(TIMER_DATA)
        if timer_manager is None:
            return []

        now = dt_util.utcnow()
        now_ns = time.monotonic_ns()
        timers = []
        for timer in timer_manager.timers.values():
            # Timers that run a command instead of ringing on a device are left out
            if timer.conversation_command:
                continue
            ends_at = None
            if timer.is_active:
                # seconds is what was left at updated_at, this keeps the end time stable between updates
                elapsed = (now_ns - timer.updated_at) / 1e9
                ends_at = (now + timedelta(seconds=timer.seconds - elapsed)).replace(microsecond=0)
            timers.append(TimerOverview(
                id=timer.id,
                device_id=timer.device_id,
                seconds=timer.created_seconds,
                ends_at=ends_at,
                name=timer.name,
                source="satellite",
            ))
        return timers

    @property
    def timers(self) -> list[TimerOverview]:
        """Return all known timers, running ones sorted by their end."""
        timers = self._native_timers() + list(self._phone_timers.values())
        return sorted(timers, key=lambda t: (t.ends_at is None, t.ends_at or dt_util.utcnow()))

    @property
    def next_timer(self) -> TimerOverview | None:
        """Return the running timer that ends next."""
        running = [t for t in self.timers if t.ends_at is not None]
        return running[0] if running else None
