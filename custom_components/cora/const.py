"""Constants for the Cora integration."""

from datetime import timedelta

DOMAIN = "cora"
CONF_URL = "server_url"
CONF_USER = "user"
CONF_PASS = "password"
CONF_NOTIFY_SERVICE = "notify_service"
CONF_MEDIA_PLAYER = "media_player"
CONF_MUSIC_APP = "music_app"
CONF_DEFAULT_PLAYLIST = "default_playlist"

DEFAULT_MUSIC_APP = "com.google.android.apps.youtube.music"

# Tokens are valid for 1 hour, refresh well before they expire
TOKEN_REFRESH_INTERVAL = timedelta(minutes=30)

ACTION_SET_TIMER = "SET_TIMER"
ACTION_SHOW_TIMER = "SHOW_TIMER"
ACTION_REMOVE_TIMER = "REMOVE_TIMER"
ACTION_OPEN_WEB = "OPEN_WEB"
ACTION_PLAY_MUSIC = "PLAY_MUSIC"
ACTION_PAUSE_MUSIC = "PAUSE_MUSIC"
ACTION_STOP_MUSIC = "STOP_MUSIC"
ACTION_STOP = "STOP"
ACTION_NEXT_MUSIC_TRACK = "NEXT_MUSIC_TRACK"
ACTION_PREVIOUS_MUSIC_TRACK = "PREVIOUS_MUSIC_TRACK"
ACTION_PLAY_MUSIC_TRACK = "PLAY_MUSIC_TRACK"
ACTION_INCREASE_VOLUME = "INCREASE_VOLUME"
ACTION_DECREASE_VOLUME = "DECREASE_VOLUME"
ACTION_SET_VOLUME = "SET_VOLUME"
ACTION_SET_ALARM = "SET_ALARM"
ACTION_REMOVE_ALARM = "REMOVE_ALARM"

# Ringing alarms stop by themselves after this time
ALARM_RING_TIMEOUT = timedelta(minutes=10)

# Pause between repeats while an alarm rings, gives time for the wake word and "stop"
ALARM_REPEAT_PAUSE = timedelta(seconds=5)

# Spoken on satellites when an alarm has no label
ALARM_DEFAULT_MESSAGE = "Wecker"

# Bundled sounds are served under this URL path
SOUNDS_URL_PATH = "/cora/sounds"

# Selectable sounds, key -> file in the sounds folder, names are in the translations
SOUNDS = {
    "classic": "classic.mp3",
    "pulse": "pulse.mp3",
    "sunrise": "sunrise.mp3",
    "lighthouse": "lighthouse.mp3",
    "marimba_morning": "marimba_morning.mp3",
    "songbird": "songbird.mp3",
    "red_alert": "red_alert.mp3",
}
DEFAULT_ALARM_SOUND = "pulse"
DEFAULT_TIMER_SOUND = "classic"

EVENT_ALARM_RINGING = "cora_alarm_ringing"
EVENT_ALARM_STOPPED = "cora_alarm_stopped"
EVENT_TIMER_RINGING = "cora_timer_ringing"
EVENT_TIMER_STOPPED = "cora_timer_stopped"
