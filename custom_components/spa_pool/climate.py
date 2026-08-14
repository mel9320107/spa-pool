"""Climate platform for direct spa temperature control."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    PRECISION_HALVES,
    PRECISION_WHOLE,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpaPoolConfigEntry
from .client import (
    SpaPoolCommandError,
    SpaPoolConnectionError,
    SpaPoolNotConnectedError,
)
from .const import DOMAIN
from .models import (
    SpaHeatMode,
    SpaHeatState,
    SpaTemperatureRange,
    SpaTemperatureUnit,
)
from .protocol import build_set_temperature_command

# Setup-parameter defaults used when optional controller configuration messages
# are not exposed by the transparent bridge.
_TEMPERATURE_LIMITS: dict[
    SpaTemperatureUnit,
    dict[SpaTemperatureRange, tuple[float, float]],
] = {
    SpaTemperatureUnit.FAHRENHEIT: {
        SpaTemperatureRange.LOW: (50.0, 99.0),
        SpaTemperatureRange.HIGH: (80.0, 104.0),
    },
    SpaTemperatureUnit.CELSIUS: {
        SpaTemperatureRange.LOW: (10.0, 37.0),
        SpaTemperatureRange.HIGH: (26.5, 40.0),
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up direct spa temperature control."""

    async_add_entities([SpaPoolClimateEntity(entry)])


class SpaPoolClimateEntity(ClimateEntity):
    """Represent measured temperature and the directly settable target.

    Heat mode is intentionally not controlled through this entity. The spa
    exposes heat mode as a cyclic panel-button command, so it is represented by
    a stateless button plus a separate state sensor instead.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE

    def __init__(self, entry: SpaPoolConfigEntry) -> None:
        """Initialise the climate entity."""

        self._entry = entry
        self._client = entry.runtime_data.client

        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Balboa-compatible",
            model="RS-485 spa controller",
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether a current status stream is available."""

        return self._client.state_available and self._client.state is not None

    @property
    @override
    def temperature_unit(self) -> str:
        """Return the temperature unit reported by the spa."""

        state = self._client.state
        if (
            state is not None
            and state.temperature_unit is SpaTemperatureUnit.FAHRENHEIT
        ):
            return UnitOfTemperature.FAHRENHEIT

        return UnitOfTemperature.CELSIUS

    @property
    @override
    def precision(self) -> float:
        """Return the precision used by the active spa temperature scale."""

        if self.temperature_unit == UnitOfTemperature.CELSIUS:
            return PRECISION_HALVES

        return PRECISION_WHOLE

    @property
    @override
    def target_temperature_step(self) -> float:
        """Return the supported target-temperature increment."""

        return self.precision

    @property
    @override
    def current_temperature(self) -> float | None:
        """Return the measured water temperature."""

        state = self._client.state
        return state.current_temperature if state is not None else None

    @property
    @override
    def target_temperature(self) -> float | None:
        """Return the target reported by the controller."""

        state = self._client.state
        return state.target_temperature if state is not None else None

    @property
    @override
    def min_temp(self) -> float:
        """Return the minimum for the active temperature range."""

        return self._temperature_limits[0]

    @property
    @override
    def max_temp(self) -> float:
        """Return the maximum for the active temperature range."""

        return self._temperature_limits[1]

    @property
    def _temperature_limits(self) -> tuple[float, float]:
        """Return limits from the current unit and low/high range."""

        state = self._client.state
        if state is None:
            return _TEMPERATURE_LIMITS[SpaTemperatureUnit.CELSIUS][
                SpaTemperatureRange.HIGH
            ]

        unit_limits = _TEMPERATURE_LIMITS.get(
            state.temperature_unit,
            _TEMPERATURE_LIMITS[SpaTemperatureUnit.CELSIUS],
        )
        return unit_limits.get(
            state.temperature_range,
            unit_limits[SpaTemperatureRange.HIGH],
        )

    @property
    @override
    def hvac_mode(self) -> HVACMode | None:
        """Expose a single thermostat mode; native heat mode has its own sensor."""

        return HVACMode.HEAT if self._client.state is not None else None

    @property
    @override
    def hvac_action(self) -> HVACAction | None:
        """Return the heater's current physical activity."""

        state = self._client.state
        if state is None:
            return None

        if state.heat_state is SpaHeatState.HEATING:
            return HVACAction.HEATING
        if state.heat_state is SpaHeatState.HEAT_WAITING:
            return HVACAction.IDLE
        if state.heat_state is SpaHeatState.OFF:
            return (
                HVACAction.OFF
                if state.heat_mode is SpaHeatMode.REST
                else HVACAction.IDLE
            )

        return None

    @override
    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Send one direct target-temperature command.

        The service succeeds once the complete frame has been written to the
        bridge. The next status frame remains the sole source of truth for the
        target actually accepted by the spa.
        """

        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return

        state = self._client.state
        if state is None or not self._client.state_available:
            raise HomeAssistantError("Spa status stream is unavailable")

        target = float(temperature)
        if not self.min_temp <= target <= self.max_temp:
            raise HomeAssistantError(
                f"Spa temperature must be between {self.min_temp:g} and "
                f"{self.max_temp:g} {self.temperature_unit}"
            )

        try:
            await self._client.async_send_frame(
                build_set_temperature_command(
                    target,
                    state.temperature_unit,
                )
            )
        except (
            SpaPoolCommandError,
            SpaPoolConnectionError,
            SpaPoolNotConnectedError,
            ValueError,
        ) as err:
            raise HomeAssistantError(
                f"Unable to send spa temperature {target:g}"
            ) from err

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to push updates from the persistent client."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.async_add_listener(self._handle_client_update)
        )

    @callback
    def _handle_client_update(self) -> None:
        """Write the latest in-memory state to Home Assistant."""

        self.async_write_ha_state()
