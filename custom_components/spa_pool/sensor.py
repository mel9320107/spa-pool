"""Sensor platform for the Spa Pool integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpaPoolConfigEntry
from .client import SpaPoolClient
from .const import (
    CONF_BLOWER_COUNT,
    CONF_LIGHT_COUNT,
    CONF_PUMP_COUNT,
    DEFAULT_BLOWER_COUNT,
    DEFAULT_LIGHT_COUNT,
    DEFAULT_PUMP_COUNT,
    DOMAIN,
    MAX_BLOWERS,
    MAX_LIGHTS,
    MAX_PUMPS,
)
from .models import SpaIntEnum, SpaState, SpaTemperatureUnit

SensorValue = str | int | float | datetime | None
ValueFn = Callable[[SpaPoolClient, SpaState | None], SensorValue]
AttributesFn = Callable[
    [SpaPoolClient, SpaState | None],
    Mapping[str, Any] | None,
]
AvailabilityFn = Callable[[SpaPoolClient, SpaState | None], bool]
IndexedStateFn = Callable[[SpaState], SpaIntEnum | None]


@dataclass(frozen=True, kw_only=True)
class SpaPoolSensorEntityDescription(SensorEntityDescription):
    """Describe a Spa Pool sensor."""

    value_fn: ValueFn
    attributes_fn: AttributesFn | None = None
    availability_fn: AvailabilityFn | None = None
    use_temperature_unit: bool = False
    message_listener: bool = False


def _state_available(
    client: SpaPoolClient,
    state: SpaState | None,
) -> bool:
    """Return whether a current decoded state is available."""

    return client.state_available and state is not None


def _message_available(
    client: SpaPoolClient,
    state: SpaState | None,
) -> bool:
    """Return whether the client has received at least one valid frame."""

    return client.last_message_at is not None


SENSOR_DESCRIPTIONS: Final[
    tuple[SpaPoolSensorEntityDescription, ...]
] = (
    SpaPoolSensorEntityDescription(
        key="current_temperature",
        translation_key="current_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda client, state: (
            state.current_temperature if state is not None else None
        ),
        availability_fn=_state_available,
        use_temperature_unit=True,
    ),
    SpaPoolSensorEntityDescription(
        key="target_temperature",
        translation_key="target_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        suggested_display_precision=1,
        value_fn=lambda client, state: (
            state.target_temperature if state is not None else None
        ),
        availability_fn=_state_available,
        use_temperature_unit=True,
    ),
    SpaPoolSensorEntityDescription(
        key="operational_state",
        translation_key="operational_state",
        icon="mdi:hot-tub",
        value_fn=lambda client, state: (
            state.operational_state.label if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"code": int(state.operational_state)}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="initialisation_mode",
        translation_key="initialisation_mode",
        icon="mdi:progress-wrench",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda client, state: (
            state.initialisation_mode.label if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"code": int(state.initialisation_mode)}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="spa_time",
        translation_key="spa_time",
        icon="mdi:clock-outline",
        value_fn=lambda client, state: (
            state.spa_time.strftime("%H:%M")
            if state is not None and state.spa_time is not None
            else None
        ),
        attributes_fn=lambda client, state: (
            {"clock_format": "24_hour" if state.clock_24_hour else "12_hour"}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="heat_mode",
        translation_key="heat_mode",
        icon="mdi:radiator",
        value_fn=lambda client, state: (
            state.heat_mode.label if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"code": int(state.heat_mode)} if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="temperature_range",
        translation_key="temperature_range",
        icon="mdi:thermometer-chevron-up",
        value_fn=lambda client, state: (
            state.temperature_range.label if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"code": int(state.temperature_range)}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="filter_mode",
        translation_key="filter_mode",
        icon="mdi:filter-outline",
        value_fn=lambda client, state: (
            state.filter_mode.label if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"code": int(state.filter_mode)}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="reminder",
        translation_key="reminder",
        icon="mdi:wrench-clock",
        value_fn=lambda client, state: (
            state.reminder.description if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {
                "code": state.reminder.code,
                "known": state.reminder.known,
                "active": state.reminder.active,
            }
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="sensor_a_temperature",
        translation_key="sensor_a_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.sensor_a_temperature if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"sensor_ab_mode": state.sensor_ab_temperatures}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
        use_temperature_unit=True,
    ),
    SpaPoolSensorEntityDescription(
        key="sensor_b_temperature",
        translation_key="sensor_b_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.sensor_b_temperature if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {"sensor_ab_mode": state.sensor_ab_temperatures}
            if state is not None
            else None
        ),
        availability_fn=_state_available,
        use_temperature_unit=True,
    ),
    SpaPoolSensorEntityDescription(
        key="hold_timer",
        translation_key="hold_timer",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.hold_timer_minutes if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="test_mode_value",
        translation_key="test_mode_value",
        icon="mdi:test-tube",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.test_mode_value if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="wifi_state",
        translation_key="wifi_state",
        icon="mdi:wifi-cog",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.wifi_state if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolSensorEntityDescription(
        key="last_valid_message",
        translation_key="last_valid_message",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: client.last_message_at,
        availability_fn=_message_available,
        message_listener=True,
    ),
    SpaPoolSensorEntityDescription(
        key="raw_status_frame",
        translation_key="raw_status_frame",
        icon="mdi:code-braces",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.raw_status_frame.hex() if state is not None else None
        ),
        attributes_fn=lambda client, state: {
            "byte_length": (
                len(state.raw_status_frame) if state is not None else 0
            )
        },
        availability_fn=_state_available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up decoded Spa Pool sensors."""

    entities: list[SensorEntity] = [
        SpaPoolSensorEntity(entry, description)
        for description in SENSOR_DESCRIPTIONS
    ]

    pump_count = _bounded_int(
        entry.options.get(CONF_PUMP_COUNT),
        default=DEFAULT_PUMP_COUNT,
        minimum=0,
        maximum=MAX_PUMPS,
    )
    entities.extend(
        SpaPoolIndexedStateSensorEntity(
            entry=entry,
            name=f"Pump {index + 1} state",
            unique_key=f"pump_{index + 1}_state",
            index=index,
            state_fn=lambda state, item=index: state.pump(item + 1),
            icon="mdi:pump",
        )
        for index in range(pump_count)
    )

    blower_count = _bounded_int(
        entry.options.get(CONF_BLOWER_COUNT),
        default=DEFAULT_BLOWER_COUNT,
        minimum=0,
        maximum=MAX_BLOWERS,
    )
    entities.extend(
        SpaPoolIndexedStateSensorEntity(
            entry=entry,
            name=f"Blower {index + 1} state",
            unique_key=f"blower_{index + 1}_state",
            index=index,
            state_fn=lambda state, item=index: (
                state.blowers[item] if item < len(state.blowers) else None
            ),
            icon="mdi:weather-windy",
        )
        for index in range(blower_count)
    )

    light_count = _bounded_int(
        entry.options.get(CONF_LIGHT_COUNT),
        default=DEFAULT_LIGHT_COUNT,
        minimum=0,
        maximum=MAX_LIGHTS,
    )
    entities.extend(
        SpaPoolIndexedStateSensorEntity(
            entry=entry,
            name=f"Light {index + 1} state",
            unique_key=f"light_{index + 1}_state",
            index=index,
            state_fn=lambda state, item=index: state.light(item + 1),
            icon="mdi:lightbulb-outline",
        )
        for index in range(light_count)
    )

    async_add_entities(entities)


class SpaPoolSensorEntity(SensorEntity):
    """Represent one decoded or diagnostic spa value."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    entity_description: SpaPoolSensorEntityDescription

    def __init__(
        self,
        entry: SpaPoolConfigEntry,
        description: SpaPoolSensorEntityDescription,
    ) -> None:
        """Initialise one sensor entity."""

        self.entity_description = description
        self._entry = entry
        self._client = entry.runtime_data.client

        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Balboa-compatible",
            model="RS-485 spa controller",
        )

    @property
    @override
    def available(self) -> bool:
        """Return availability appropriate to this sensor."""

        state = self._client.state
        availability_fn = self.entity_description.availability_fn

        if availability_fn is not None:
            return availability_fn(self._client, state)

        return self._client.state_available

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return the latest value from memory."""

        return self.entity_description.value_fn(
            self._client,
            self._client.state,
        )

    @property
    @override
    def native_unit_of_measurement(self) -> str | None:
        """Return the active spa temperature unit where applicable."""

        if not self.entity_description.use_temperature_unit:
            return self.entity_description.native_unit_of_measurement

        state = self._client.state
        if state is None:
            return None

        if state.temperature_unit is SpaTemperatureUnit.CELSIUS:
            return "°C"
        if state.temperature_unit is SpaTemperatureUnit.FAHRENHEIT:
            return "°F"

        return None

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return stable supporting attributes for the current value."""

        attributes_fn = self.entity_description.attributes_fn
        if attributes_fn is None:
            return None

        return attributes_fn(self._client, self._client.state)

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to the relevant push-update channel."""

        await super().async_added_to_hass()

        if self.entity_description.message_listener:
            remove_listener = self._client.async_add_message_listener(
                self._handle_client_update
            )
        else:
            remove_listener = self._client.async_add_listener(
                self._handle_client_update
            )

        self.async_on_remove(remove_listener)

    @callback
    def _handle_client_update(self) -> None:
        """Write the latest in-memory value to Home Assistant."""

        self.async_write_ha_state()


class SpaPoolIndexedStateSensorEntity(SensorEntity):
    """Report the latest decoded state of one toggle-controlled item."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        *,
        entry: SpaPoolConfigEntry,
        name: str,
        unique_key: str,
        index: int,
        state_fn: IndexedStateFn,
        icon: str,
    ) -> None:
        """Initialise one read-only state sensor."""

        self._entry = entry
        self._client = entry.runtime_data.client
        self._index = index
        self._state_fn = state_fn

        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_key}"
        self._attr_icon = icon
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Balboa-compatible",
            model="RS-485 spa controller",
        )

    def _decoded_value(self) -> SpaIntEnum | None:
        """Return the decoded enum from the latest in-memory state."""

        state = self._client.state
        return self._state_fn(state) if state is not None else None

    @property
    @override
    def available(self) -> bool:
        """Return whether this field is present in the current status frame."""

        return (
            self._client.state_available
            and self._decoded_value() is not None
        )

    @property
    @override
    def native_value(self) -> str | None:
        """Return the controller-reported state label."""

        value = self._decoded_value()
        return value.label if value is not None else None

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Expose the underlying protocol code."""

        value = self._decoded_value()
        return {"code": int(value)} if value is not None else None

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to push updates from the persistent client."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.async_add_listener(self._handle_client_update)
        )

    @callback
    def _handle_client_update(self) -> None:
        """Write the newest decoded state to Home Assistant."""

        self.async_write_ha_state()


def _bounded_int(
    value: object,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Return a bounded integer option or a safe default."""

    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        return default

    return min(max(parsed, minimum), maximum)
