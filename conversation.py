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
from homeassistant.helpers import intent

from .actions import async_handle_action
from .auth import CoraAuth, call_post_request

_LOGGER = logging.getLogger(__name__)


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

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process a sentence."""

        await self.auth.ensure_token()

        content = {'message': user_input.text, 'speakerIdent': "", "sessionToken": self.auth.token, 'conversation_id': user_input.conversation_id, 'device_id': user_input.device_id, 'language': user_input.language, 'agent_id': user_input.agent_id, 'extra_system_prompt': user_input.extra_system_prompt}

        _LOGGER.info("Content sent to endpoint %s", content)
        url = self.auth.url + "/SpeechServlet"

        try:
            _LOGGER.info(f"url: {url}")
            result_req = await call_post_request(url, self.auth.basic_auth, content)
            result = json.loads(result_req)
            if not "speakResponse" in result:
                _LOGGER.warning("Empty response, check password and username")
                raise aiohttp.ClientError()
        except aiohttp.ClientError:
            _LOGGER.warning("Unable to connect to endpoint " + url)
            result = {
                "finish_reason": "error",
                "speakResponse": "Sorry, unable to connect to endpoint. Check settings and try again."
            }
            self.auth.reset_token()
        except json.decoder.JSONDecodeError as e:
            _LOGGER.warning(f"Something happened: {e}")
            _LOGGER.warning(f"Result: {result_req}")
            result = {
                "finish_reason": "error",
                "speakResponse": "Sorry, I didn't get a response from endpoint. Check your logs for possible issues."
            }
            self.auth.reset_token()

        _LOGGER.info(f"Result %s", result)

        await async_handle_action(self.hass, self.entry, user_input, result)

        # https://github.com/home-assistant/core/blob/220aaf93c6b0d201bb4baa59d96ff9d9c8a66279/homeassistant/helpers/intent.py#L1380
        intent_response = intent.IntentResponse(language=user_input.language)

        shouldContinue = False
        if "speakResponse" in result and result["speakResponse"] != "":
            intent_response.async_set_speech(result["speakResponse"])
            shouldContinue = False # result["continue_conversation"]
        else:
            intent_response.async_set_speech(result["speakResponse"])

        _LOGGER.debug(f"shouldContinue {shouldContinue}")
        # https://github.com/home-assistant/core/blob/eb3cb0e0c7835ca10cdbb225d85f5e22d512e290/homeassistant/components/conversation/models.py#L60
        return conversation.ConversationResult(
            response=intent_response, conversation_id=user_input.conversation_id, continue_conversation=shouldContinue,
        )
