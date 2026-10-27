# Cora

Home Assistant conversation agent that forwards Assist requests to a Cora endpoint.
Responses can trigger actions on the requesting device, like timers, music and volume
on an Android phone running the Home Assistant companion app, or on a media player.

Requires Home Assistant 2026.3 or newer.

## Installation

### HACS

1. In HACS open the menu in the top right and choose **Custom repositories**.
2. Add the URL of this repository with the type **Integration**.
3. Search for **Cora**, download it and restart Home Assistant.
4. Go to **Settings → Devices & services → Add integration** and add **Cora**.

Updates show up in HACS like for any other integration.

### Manual

Copy `custom_components/cora` to `/config/custom_components/cora` and restart Home Assistant.

## Configuration

| Setting | Description |
|---|---|
| Conversation Endpoint | URL of the Cora server |
| Endpoint Username / Password | Login for the Cora server |
| Fallback notify service for timers | Phone used when the request does not come from a phone, e.g. `mobile_app_pixel_8` |
| Fallback media player for music | Media player used when the requesting device has none, e.g. `media_player.kitchen` |
| Music app on the phone | Package name of the music app, defaults to YouTube Music |
| Default playlist | Played by `PLAY_MUSIC` when nothing is playing, random songs if empty |

Select **Cora** as the conversation agent of your voice assistant under
**Settings → Voice assistants**.

## Actions

The Cora server can return an `action` with `actionParameters` next to `speakResponse`:

| Action | Parameters |
|---|---|
| `SET_TIMER` | seconds |
| `SHOW_TIMER` | – |
| `REMOVE_TIMER` | – (dismisses a ringing timer, cancels the timers of a satellite) |
| `SET_ALARM` | `"HH:MM"`, days (optional, e.g. `"MON,TUE,WED,THU,FRI"`), label (optional) |
| `REMOVE_ALARM` | `"HH:MM"` (optional, without it the next alarm is removed) |
| `STOP` | – (stops a ringing alarm, otherwise dismisses ringing timers and stops running music) |
| `OPEN_WEB` | URL |
| `PLAY_MUSIC` | playlist (optional) |
| `PAUSE_MUSIC` / `STOP_MUSIC` | – |
| `NEXT_MUSIC_TRACK` / `PREVIOUS_MUSIC_TRACK` | – |
| `PLAY_MUSIC_TRACK` | track, artist (optional) |
| `SET_VOLUME` | 0–100 |
| `INCREASE_VOLUME` / `DECREASE_VOLUME` | step in percent (optional, default 10) |

Phone actions need the Android companion app with **notification access** and
**display over other apps**. Enable the **Music volume** and **Media session** sensors
in the companion app for volume control and playback detection.

## Timers

Satellites that support Home Assistant timers get a native timer. ESPHome satellites
like the Voice PE ring it themselves and stop it with "stop". Other satellites, like
Wyoming satellites, have no timer sound of their own, so Cora rings the finished timer
like an alarm until you say "stop" (event `cora_timer_ringing` / `cora_timer_stopped`).
All other requests start a timer in the clock app of the phone, or the fallback phone.

The **Next timer** sensor of the Cora device shows when the next timer ends and lists
all timers in its attributes. Timers in the phone's clock app are only known if Cora
started them and are forgotten after a restart of Home Assistant.

## Alarms

Examples for `SET_ALARM`:

| Parameters | Alarm |
|---|---|
| `["07:30"]` | once, at the next 07:30 |
| `["07:30", "MON,TUE,WED,THU,FRI"]` | every weekday at 07:30 |
| `["07:30", "", "Arbeit"]` | once, with the label "Arbeit" |

Requests from a phone set the alarm in its clock app, as do requests without a device
when a fallback phone is configured. For all other devices Home Assistant keeps the alarm
itself. It survives restarts, but an alarm missed while Home Assistant was down does not
ring late. When the alarm rings, a satellite announces the label (or "Wecker"), otherwise
a media player on the device, in its room or the fallback player plays an alarm sound.
This repeats until you say "stop" or 10 minutes have passed.

### Sounds

The **Alarm sound** and **Timer sound** dropdowns of the Cora device choose what rings:

| Sound | Character |
|---|---|
| Classic | the original timer sound (default for timers) |
| Pulse | steady beeping, clearly audible but not harsh (default for alarms) |
| Sunrise | rising chime arpeggio |
| Lighthouse | slow two tone bell |
| Marimba Morning | cheerful marimba phrase |
| Songbird | bursts of bird like chirps |
| Red Alert | fast, urgent beeper for deep sleepers |

The sounds are generated with `tools/generate_sounds.py`.

The **Next alarm** sensor of the Cora device shows when the next alarm rings and
lists all alarms in its attributes.

Ringing alarms fire the events `cora_alarm_ringing` and `cora_alarm_stopped` with
`alarm_id`, `time`, `label`, `device_id` and `area_id`, so you can build automations on them:

```yaml
automation:
  - alias: Lights on when the alarm rings
    triggers:
      - trigger: event
        event_type: cora_alarm_ringing
        event_data:
          area_id: bedroom
    actions:
      - action: light.turn_on
        target:
          area_id: bedroom
        data:
          brightness_pct: 60
```
