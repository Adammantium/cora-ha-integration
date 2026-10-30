"""Conversation agent that forwards requests to the Cora endpoint."""
from __future__ import annotations

import json
import logging
from typing import Literal

import aiohttp

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, intent
from homeassistant.util.ulid import ulid_now

from .actions import async_handle_action
from .auth import CoraAuth, call_post_request

_LOGGER = logging.getLogger(__name__)

# Spoken when the endpoint can not be reached or sends an invalid response, by language
ERROR_MESSAGES = {
    "en": {
        "cannot_connect": "Sorry, unable to connect to endpoint. Check settings and try again.",
        "invalid_response": "Sorry, I didn't get a response from endpoint. Check your logs for possible issues.",
    },
    "de": {
        "cannot_connect": "Entschuldigung, ich kann den Server nicht erreichen. Prüfe die Einstellungen und versuche es erneut.",
        "invalid_response": "Entschuldigung, ich habe keine gültige Antwort vom Server bekommen. Prüfe die Logs auf mögliche Fehler.",
    },
}


def error_message(language: str | None, key: str) -> str:
    """Return the error message in the language of the request, English if it is not translated."""
    messages = ERROR_MESSAGES.get((language or "en").split("-")[0].lower(), ERROR_MESSAGES["en"])
    return messages[key]


class CoraAgent(conversation.AbstractConversationAgent):
    """Cora conversation agent."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, auth: CoraAuth) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self.auth = auth
        _LOGGER.debug("configUrl %s", auth.url)
        _LOGGER.debug("configUser %s", auth.user)
        _LOGGER.debug("configPass %s", auth.password)

    def get_device_info(self, device_id: str | None) -> tuple[str | None, str | None]:
        """Return the name and room (area) of the device that sent the request."""
        if not device_id:
            return None, None
        device = dr.async_get(self.hass).async_get(device_id)
        if device is None:
            return None, None

        room = None
        if device.area_id and (area := ar.async_get(self.hass).async_get_area(device.area_id)):
            room = area.name
        return device.name_by_user or device.name, room

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process a sentence."""

        await self.auth.ensure_token()

        device_name, room = self.get_device_info(user_input.device_id)

        # Follow-up questions are sent with the same id, so the endpoint can match the answer to the question
        conversation_id = user_input.conversation_id or ulid_now()

        content = {'message': user_input.text, 'speakerIdent': "", "sessionToken": self.auth.token, 'conversation_id': conversation_id, 'device_id': user_input.device_id, 'device_name': device_name, 'room': room, 'language': user_input.language, 'agent_id': user_input.agent_id, 'extra_system_prompt': user_input.extra_system_prompt}

        _LOGGER.info("Content sent to endpoint %s", content)
        url = self.auth.url + "/SpeechServlet"

        try:
            _LOGGER.info(f"url: {url}")
            result_req = await call_post_request(url, self.auth.basic_auth, content)
            result = json.loads(result_req)
            if not "speakResponse" in result:
                _LOGGER.warning("Empty response, check password and username")
                raise aiohttp.ClientError()
            self.auth.set_connected(True)
        except (aiohttp.ClientError, TimeoutError):
            _LOGGER.warning("Unable to connect to endpoint " + url)
            result = {
                "finish_reason": "error",
                "speakResponse": error_message(user_input.language, "cannot_connect")
            }
            self.auth.reset_token()
            self.auth.set_connected(False)
        except json.decoder.JSONDecodeError as e:
            _LOGGER.warning(f"Something happened: {e}")
            _LOGGER.warning(f"Result: {result_req}")
            result = {
                "finish_reason": "error",
                "speakResponse": error_message(user_input.language, "invalid_response")
            }
            self.auth.reset_token()
            self.auth.set_connected(False)

        _LOGGER.info(f"Result %s", result)

        await async_handle_action(self.hass, self.entry, user_input, result)

        # https://github.com/home-assistant/core/blob/220aaf93c6b0d201bb4baa59d96ff9d9c8a66279/homeassistant/helpers/intent.py#L1380
        intent_response = intent.IntentResponse(language=user_input.language)

        intent_response.async_set_speech(result.get("speakResponse") or "")

        # The endpoint asks a question, listen for the answer without the wake word
        should_continue = bool(result.get("requiresResponse"))

        _LOGGER.debug(f"shouldContinue {should_continue}")
        # https://github.com/home-assistant/core/blob/eb3cb0e0c7835ca10cdbb225d85f5e22d512e290/homeassistant/components/conversation/models.py#L60
        return conversation.ConversationResult(
            response=intent_response, conversation_id=conversation_id, continue_conversation=should_continue,
        )
