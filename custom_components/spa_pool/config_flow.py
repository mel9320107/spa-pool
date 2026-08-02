"""Config flow for the Spa Pool integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)

from .client import SpaPoolClient, SpaPoolConnectionError
from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)
_ENTRY_TITLE = "Spa Pool"

_CONNECTION_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): TextSelector(),
        vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
            NumberSelectorConfig(
                min=1,
                max=65535,
                step=1,
                mode=NumberSelectorMode.BOX,
            )
        ),
    }
)


def _normalise_connection_data(user_input: dict[str, Any]) -> dict[str, Any]:
    """Return canonical connection settings."""

    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
    }


async def _async_validate_connection(data: dict[str, Any]) -> None:
    """Confirm that the bridge supplies at least one valid status frame."""

    client = SpaPoolClient(
        host=data[CONF_HOST],
        port=data[CONF_PORT],
    )

    try:
        await client.async_start()
    finally:
        await client.async_stop()


class SpaPoolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle configuration for Spa Pool."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Set up a spa TCP bridge."""

        errors: dict[str, str] = {}

        if user_input is not None:
            data = _normalise_connection_data(user_input)

            if self._connection_is_configured(
                data[CONF_HOST],
                data[CONF_PORT],
            ):
                return self.async_abort(reason="already_configured")

            try:
                await _async_validate_connection(data)
            except (SpaPoolConnectionError, OSError, TimeoutError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception(
                    "Unexpected error while validating the spa bridge"
                )
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=_ENTRY_TITLE,
                    data=data,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=_CONNECTION_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Allow the bridge address to be changed without removing the entry."""

        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _normalise_connection_data(user_input)

            if self._connection_is_configured(
                data[CONF_HOST],
                data[CONF_PORT],
                exclude_entry_id=entry.entry_id,
            ):
                errors["base"] = "already_configured"
            else:
                try:
                    await _async_validate_connection(data)
                except (SpaPoolConnectionError, OSError, TimeoutError):
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception(
                        "Unexpected error while validating the spa bridge"
                    )
                    errors["base"] = "unknown"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates=data,
                        reload_even_if_entry_is_unchanged=False,
                    )

        schema = self.add_suggested_values_to_schema(
            _CONNECTION_SCHEMA,
            {
                CONF_HOST: entry.data[CONF_HOST],
                CONF_PORT: entry.data.get(CONF_PORT, DEFAULT_PORT),
            },
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    def _connection_is_configured(
        self,
        host: str,
        port: int,
        *,
        exclude_entry_id: str | None = None,
    ) -> bool:
        """Return whether another entry already uses this bridge endpoint."""

        normalised_host = host.casefold()

        return any(
            entry.entry_id != exclude_entry_id
            and str(entry.data.get(CONF_HOST, "")).strip().casefold()
            == normalised_host
            and int(entry.data.get(CONF_PORT, DEFAULT_PORT)) == port
            for entry in self.hass.config_entries.async_entries(DOMAIN)
        )
