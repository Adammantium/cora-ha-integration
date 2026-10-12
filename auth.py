"""Authentication against the Cora endpoint."""
from __future__ import annotations

import base64
from collections.abc import Callable
import json
import logging

import aiohttp

_LOGGER = logging.getLogger(__name__)


async def call_post_request(url: str, auth: str, data: dict, raise_for_status: bool = False) -> str:
    """POST json data to the endpoint and return the response text.

    With raise_for_status an error status raises aiohttp.ClientResponseError.
    """
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(verify_ssl=False)) as session:
        if auth and len(auth) > 0:
            session.headers.update({"Authorization": f"Basic {auth}"})
        async with session.post(url, json=data, raise_for_status=raise_for_status) as response:
            text = await response.text()
            _LOGGER.info(f"Post Result text: {text}")
            return text


class CoraAuth:
    """Holds the credentials, session token and connection state for the Cora endpoint."""

    def __init__(self, url: str, user: str, password: str) -> None:
        """Initialize the authentication."""
        self.url = url
        self.user = user
        self.password = password
        self.token = ""
        self.connected: bool | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def basic_auth(self) -> str:
        """Return the base64 encoded basic auth credentials."""
        return base64.b64encode(f"{self.user}:{self.password}".encode()).decode()

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call listener when the connection state changes, returns a function to remove it."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def set_connected(self, connected: bool) -> None:
        """Update the connection state and notify listeners."""
        if self.connected == connected:
            return
        self.connected = connected
        _LOGGER.info(f"Connection state: {'connected' if connected else 'disconnected'}")
        for listener in self._listeners:
            listener()

    def reset_token(self) -> None:
        """Forget the session token so a new one is requested next time."""
        self.token = ""

    async def ensure_token(self) -> None:
        """Request a session token if none is set."""
        if self.token == "" or len(self.token) == 0:
            await self.get_token()

    async def get_token(self) -> None:
        """Log in to the endpoint and store the session token."""
        content = {'username': self.user, 'password': self.password}

        try:
            _LOGGER.info(f"url: {self.url}/LoginServlet")
            result_req = await call_post_request(url=self.url + "/LoginServlet", auth="", data=content)
            result = json.loads(result_req)
        except (aiohttp.ClientError, TimeoutError):
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
            self.set_connected(True)
        else:
            # Keep the old token, it might still be valid until a request fails
            self.set_connected(False)

    async def refresh_token(self) -> None:
        """Extend the session token, log in again if it is no longer valid."""
        if self.token == "" or len(self.token) == 0:
            await self.get_token()
            return

        try:
            _LOGGER.info(f"url: {self.url}/RefreshServlet")
            result_req = await call_post_request(url=self.url + "/RefreshServlet", auth="", data={'token': self.token}, raise_for_status=True)
            result = json.loads(result_req)
        except aiohttp.ClientResponseError as e:
            # 401 means the token expired
            _LOGGER.info(f"Token refresh rejected ({e.status}), logging in again")
            result = {}
        except (aiohttp.ClientError, TimeoutError, json.decoder.JSONDecodeError) as e:
            _LOGGER.warning(f"Token refresh failed: {e}")
            result = {}

        if isinstance(result, dict) and result.get("status") == "OK":
            _LOGGER.info("TOKEN REFRESHED")
            self.set_connected(True)
        else:
            await self.get_token()
