"""Diagnostics support for the Spa Pool integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from . import SpaPoolConfigEntry

_TO_REDACT = {CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
) -> dict[str, Any]:
    """Return a redacted diagnostic snapshot for one spa config entry."""

    client = entry.runtime_data.client
    state = client.state
    fault = client.last_fault

    return {
        "config_entry": {
            "title": entry.title,
            "version": entry.version,
            "minor_version": entry.minor_version,
            "data": async_redact_data(dict(entry.data), _TO_REDACT),
            "options": dict(entry.options),
        },
        "transport": client.diagnostics(),
        "state": (
            state.as_dict(include_raw=True)
            if state is not None
            else None
        ),
        "last_fault": (
            fault.as_dict(include_raw=True)
            if fault is not None
            else None
        ),
        "last_valid_frame": (
            client.last_frame.hex()
            if client.last_frame is not None
            else None
        ),
    }
