"""Home Assistant integration for a Balboa-compatible spa TCP bridge."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import TypeAlias

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .client import SpaPoolClient, SpaPoolConnectionError
from .const import DEFAULT_PORT

_LOGGER = logging.getLogger(__name__)
_ENTRY_TITLE = "Spa Pool"

PLATFORMS: tuple[Platform, ...] = (
    Platform.CLIMATE,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.EVENT,
    Platform.BUTTON,
)


@dataclass(slots=True)
class SpaPoolRuntimeData:
    """Objects retained for the lifetime of one config entry."""

    client: SpaPoolClient


SpaPoolConfigEntry: TypeAlias = ConfigEntry[SpaPoolRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
) -> bool:
    """Set up Spa Pool from a config entry."""

    # Older builds included the bridge IP address in the config-entry title.
    # Normalise it here so existing installations acquire clean device/entity
    # display names without requiring the integration to be removed.
    if entry.title != _ENTRY_TITLE:
        hass.config_entries.async_update_entry(entry, title=_ENTRY_TITLE)

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    client = SpaPoolClient(host=host, port=port)

    try:
        await client.async_start()
    except (SpaPoolConnectionError, OSError, TimeoutError) as err:
        await client.async_stop()
        raise ConfigEntryNotReady(
            f"Unable to connect to the spa bridge at {host}:{port}"
        ) from err

    entry.runtime_data = SpaPoolRuntimeData(client=client)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await client.async_stop()
        raise

    _LOGGER.info("Connected to spa bridge at %s:%s", host, port)
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
) -> bool:
    """Unload the integration without restarting Home Assistant."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unloaded:
        return False

    await entry.runtime_data.client.async_stop()
    return True


async def _async_update_listener(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
) -> None:
    """Reload the integration after host, port, or options change."""

    await hass.config_entries.async_reload(entry.entry_id)
