"""The Cora integration."""

# This code is from nodered_conversation and then modified for my needs
# https://github.com/roblandry/nodered_conversation
from __future__ import annotations

from functools import partial
import logging
import aiohttp
import json
import base64
from typing import Literal

import voluptuous as vol
import asyncio

from homeassistant.components import conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import MATCH_ALL

from homeassistant.core import (
    HomeAssistant,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr, intent
from homeassistant.util import slugify, ulid

from .const import (
    ACTION_SET_TIMER,
    CONF_URL,
    CONF_USER,
    CONF_PASS,
    CONF_NOTIFY_SERVICE,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Cora from a config entry."""

    #hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry.data[CONF_URL]

    _LOGGER.info(f"entry.data: {entry.data}")
    
    user = ""
    password = ""
    if CONF_USER in entry.data:
        user = entry.data[CONF_USER]
    if CONF_PASS in entry.data:
        password = entry.data[CONF_PASS]
    
    conversation.async_set_agent(hass, entry, CoraAgent(hass, entry, entry.data[CONF_URL], user, password ))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data[DOMAIN].pop(entry.entry_id)
    
    conversation.async_unset_agent(hass, entry)
    return True


class CoraAgent(conversation.AbstractConversationAgent):
    """Cora conversation agent."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, configUrl, configUser, configPass) -> None:
        """Initialize the agent."""
        self.hass = hass
        self.entry = entry
        self.url=configUrl
        self.user=configUser
        self.password=configPass
        self.token=""
        _LOGGER.debug("configUrl %s", configUrl)
        _LOGGER.debug("configUser %s", configUser)
        _LOGGER.debug("configPass %s", configPass)

    async def get_token(self):
        content = {'username': self.user, 'password': self.password}
        
        try:
            _LOGGER.info(f"url: {self.url}/LoginServlet")
            result_req = await self.call_post_request(url=self.url + "/LoginServlet", auth="", data=content)
            result = json.loads(result_req)
        except aiohttp.ClientError:
            _LOGGER.warning("Unable to connect to endpoint " + self.url + "/LoginServlet")
            result = {
                "finish_reason": "error",
                "message": "Sorry, unable to connect to endpoint. Check settings and try again." 
            }
        except json.decoder.JSONDecodeError as e:
            _LOGGER.warning(f"Something happened: {e}")
            _LOGGER.warning(f"Result: {result_req}")
            result = {
                "finish_reason": "error",
                "message": "Sorry, I didn't get a response from endpoint. Check your logs for possible issues."
            }
        
        if "status" in result and result["status"] != "" and len(result["status"]) > 10:
            self.token = result["status"]
            _LOGGER.info("TOKEN SET")

    def get_notify_service(self, device_id: str | None) -> str | None:
        """Find the mobile_app notify service for the device that sent the request."""
        if device_id:
            device = dr.async_get(self.hass).async_get(device_id)
            if device:
                for entry_id in device.config_entries:
                    entry = self.hass.config_entries.async_get_entry(entry_id)
                    if entry and entry.domain == "mobile_app" and "device_name" in entry.data:
                        service = f"mobile_app_{slugify(entry.data['device_name'])}"
                        if self.hass.services.has_service("notify", service):
                            return service

        # Fall back to the notify service configured for this integration
        service = self.entry.data.get(CONF_NOTIFY_SERVICE, "").removeprefix("notify.")
        if service and self.hass.services.has_service("notify", service):
            return service
        return None

    async def set_timer(self, device_id: str | None, parameters: list):
        """Tell the companion app on the phone to start a system timer."""
        try:
            seconds = int(parameters[0])
        except (IndexError, ValueError, TypeError):
            _LOGGER.warning(f"Invalid SET_TIMER parameters: {parameters}")
            return

        service = self.get_notify_service(device_id)
        if service is None:
            _LOGGER.warning(f"No mobile_app notify service found for device {device_id}, can not set timer")
            return

        _LOGGER.info(f"Setting timer for {seconds}s via notify.{service}")
        # https://companion.home-assistant.io/docs/notifications/notification-commands/#activity
        try:
            await self.hass.services.async_call(
                "notify",
                service,
                {
                    "message": "command_activity",
                    "data": {
                        "intent_action": "android.intent.action.SET_TIMER",
                        "intent_extras": f"android.intent.extra.alarm.LENGTH:{seconds},android.intent.extra.alarm.SKIP_UI:true",
                    },
                },
                blocking=True,
            )
        except Exception as e:  # pylint: disable=broad-except
            _LOGGER.warning(f"Failed to set timer: {e}")

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Return a list of supported languages."""
        return MATCH_ALL
    
    async def call_post_request(self, url: str, auth: str, data: dict):
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
            if auth and len(auth) > 0:
                session.headers.update({"Authorization": f"Basic {auth}"})
            async with session.post(url, json=data) as response:
                text = await response.text()
                #_LOGGER.warning(f"Post Result: {response}")
                _LOGGER.info(f"Post Result text: {text}")
                return text

    async def async_process(
        self, user_input: conversation.ConversationInput
    ) -> conversation.ConversationResult:
        """Process a sentence."""

        if self.token == "" or len(self.token) == 0:
            await self.get_token()
        
        content = {'message': user_input.text, 'speakerIdent': "", "sessionToken": self.token, 'conversation_id': user_input.conversation_id, 'device_id': user_input.device_id, 'language': user_input.language, 'agent_id': user_input.agent_id, 'extra_system_prompt': user_input.extra_system_prompt}
        
        _LOGGER.info("Content sent to endpoint %s", content)
        auth = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        
        try:
            _LOGGER.info(f"url: {self.url}/SpeechServlet")
            result_req = await self.call_post_request(self.url + "/SpeechServlet", auth, content)
            result = json.loads(result_req)
            if not "speakResponse" in result:
                _LOGGER.warning("Empty response, check password and username")
                raise aiohttp.ClientError()
        except aiohttp.ClientError:
            _LOGGER.warning("Unable to connect to endpoint " + self.url + "/SpeechServlet")
            result = {
                "finish_reason": "error",
                "speakResponse": "Sorry, unable to connect to endpoint. Check settings and try again." 
            }
            self.token = ""
        except json.decoder.JSONDecodeError as e:
            _LOGGER.warning(f"Something happened: {e}")
            _LOGGER.warning(f"Result: {result_req}")
            result = {
                "finish_reason": "error",
                "speakResponse": "Sorry, I didn't get a response from endpoint. Check your logs for possible issues."
            }
            self.token = ""

        _LOGGER.info(f"Result %s", result)

        if str(result.get("action", "")).upper() == ACTION_SET_TIMER:
            await self.set_timer(user_input.device_id, result.get("actionParameters") or [])

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
            #response=intent_response, conversation_id=user_input.conversation_id, 
        )

