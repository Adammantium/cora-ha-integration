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
| `REMOVE_TIMER` | – (dismisses a ringing timer) |
| `STOP` | – (dismisses ringing timers and stops running music) |
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
