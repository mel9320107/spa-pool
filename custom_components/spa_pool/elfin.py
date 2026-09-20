"""Elfin EW11 management helpers."""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import BasicAuth, ClientConnectionError, ClientSession, ServerDisconnectedError

_LOGGER = logging.getLogger(__name__)

_ELFIN_RESTART_CID = 20003
_ELFIN_WEB_PORT = 80
_ELFIN_USERNAME = "admin"
_ELFIN_PASSWORD = "admin"
_ELFIN_REQUEST_TIMEOUT = 5.0


class ElfinRestartError(Exception):
    """Raised when the Elfin restart request cannot be sent."""


async def async_restart_elfin(
    session: ClientSession,
    host: str,
) -> None:
    """Restart an Elfin EW11 through its local management API.

    The request mirrors the EW11 web interface's Restart button:
    ``POST /cmd`` with ``CID 20003`` and an empty payload. Stock EW11
    firmware protects the endpoint with HTTP Basic authentication.

    A server-side disconnect after the request has been submitted is expected:
    the adapter may reboot before it finishes the HTTP response.
    """

    url = f"http://{host}:{_ELFIN_WEB_PORT}/cmd"
    body = "msg=" + json.dumps(
        {"CID": _ELFIN_RESTART_CID, "PL": {}},
        separators=(",", ":"),
    )

    try:
        async with asyncio.timeout(_ELFIN_REQUEST_TIMEOUT):
            async with session.post(
                url,
                data=body,
                headers={"Content-Type": "application/json;charset=utf-8"},
                auth=BasicAuth(_ELFIN_USERNAME, _ELFIN_PASSWORD),
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise ElfinRestartError(
                        "Elfin management credentials were rejected"
                    )
                if response.status >= 400:
                    raise ElfinRestartError(
                        f"Elfin restart request returned HTTP {response.status}"
                    )
                await response.read()
    except ServerDisconnectedError:
        _LOGGER.debug(
            "Elfin %s disconnected while processing the restart request",
            host,
        )
    except (ClientConnectionError, TimeoutError) as err:
        raise ElfinRestartError(
            f"Unable to reach the Elfin management interface at {host}"
        ) from err
