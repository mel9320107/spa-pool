"""Binary sensor platform for the Spa Pool integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpaPoolConfigEntry
from .client import SpaPoolClient
from .const import DOMAIN
from .models import SpaFilterMode, SpaState

ValueFn = Callable[[SpaPoolClient, SpaState | None], bool | None]
AttributesFn = Callable[
    [SpaPoolClient, SpaState | None],
    Mapping[str, Any] | None,
]
AvailabilityFn = Callable[[SpaPoolClient, SpaState | None], bool]


@dataclass(frozen=True, kw_only=True)
class SpaPoolBinarySensorEntityDescription(
    BinarySensorEntityDescription
):
    """Describe one Spa Pool binary sensor."""

    value_fn: ValueFn
    attributes_fn: AttributesFn | None = None
    availability_fn: AvailabilityFn | None = None


def _always_available(
    client: SpaPoolClient,
    state: SpaState | None,
) -> bool:
    """Keep the connectivity entity available while the entry is loaded."""

    return True


def _state_available(
    client: SpaPoolClient,
    state: SpaState | None,
) -> bool:
    """Return whether a current decoded state is available."""

    return client.state_available and state is not None


BINARY_SENSOR_DESCRIPTIONS: Final[
    tuple[SpaPoolBinarySensorEntityDescription, ...]
] = (
    SpaPoolBinarySensorEntityDescription(
        key="status_stream",
        translation_key="status_stream",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda client, state: client.transport_available,
        attributes_fn=lambda client, state: {
            "tcp_connected": client.connected,
            "state_available": client.state_available,
            "last_valid_message": (
                client.last_message_at.isoformat()
                if client.last_message_at is not None
                else None
            ),
            "last_status_message": (
                client.last_status_at.isoformat()
                if client.last_status_at is not None
                else None
            ),
        },
        availability_fn=_always_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="heating",
        translation_key="heating",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda client, state: (
            state.is_heating if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {
                "heat_state": state.heat_state.label,
                "heat_state_code": int(state.heat_state),
            }
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="circulation_pump",
        translation_key="circulation_pump",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda client, state: (
            state.circulation_pump if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="filter_cycle_1",
        translation_key="filter_cycle_1",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:filter",
        value_fn=lambda client, state: (
            state.filter_mode
            in (
                SpaFilterMode.CYCLE_1,
                SpaFilterMode.CYCLE_1_AND_2,
            )
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="filter_cycle_2",
        translation_key="filter_cycle_2",
        device_class=BinarySensorDeviceClass.RUNNING,
        icon="mdi:filter",
        value_fn=lambda client, state: (
            state.filter_mode
            in (
                SpaFilterMode.CYCLE_2,
                SpaFilterMode.CYCLE_1_AND_2,
            )
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="panel_locked",
        translation_key="panel_locked",
        icon="mdi:lock",
        value_fn=lambda client, state: (
            state.panel_locked if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="settings_locked",
        translation_key="settings_locked",
        icon="mdi:lock-cog",
        value_fn=lambda client, state: (
            state.settings_locked if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="reminder_active",
        translation_key="reminder_active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:wrench-clock",
        value_fn=lambda client, state: (
            state.has_active_reminder if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {
                "description": state.reminder.description,
                "code": state.reminder.code,
                "known": state.reminder.known,
            }
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="timeouts_active",
        translation_key="timeouts_active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.timeouts_active if state is not None else None
        ),
        availability_fn=_state_available,
    ),
    SpaPoolBinarySensorEntityDescription(
        key="sensor_ab_mode",
        translation_key="sensor_ab_mode",
        icon="mdi:thermometer-lines",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda client, state: (
            state.sensor_ab_temperatures if state is not None else None
        ),
        attributes_fn=lambda client, state: (
            {
                "sensor_a_temperature": state.sensor_a_temperature,
                "sensor_b_temperature": state.sensor_b_temperature,
            }
            if state is not None
            else None
        ),
        availability_fn=_state_available,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Spa Pool binary sensors."""

    async_add_entities(
        SpaPoolBinarySensorEntity(entry, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class SpaPoolBinarySensorEntity(BinarySensorEntity):
    """Represent one Boolean spa condition."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    entity_description: SpaPoolBinarySensorEntityDescription

    def __init__(
        self,
        entry: SpaPoolConfigEntry,
        description: SpaPoolBinarySensorEntityDescription,
    ) -> None:
        """Initialise one binary sensor."""

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
        """Return availability appropriate to this condition."""

        availability_fn = self.entity_description.availability_fn
        if availability_fn is None:
            return self._client.transport_available

        return availability_fn(self._client, self._client.state)

    @property
    @override
    def is_on(self) -> bool | None:
        """Return the latest Boolean value from memory."""

        return self.entity_description.value_fn(
            self._client,
            self._client.state,
        )

    @property
    @override
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        """Return supporting protocol information."""

        attributes_fn = self.entity_description.attributes_fn
        if attributes_fn is None:
            return None

        return attributes_fn(self._client, self._client.state)

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to push updates from the persistent client."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.async_add_listener(self._handle_client_update)
        )

    @callback
    def _handle_client_update(self) -> None:
        """Write the latest in-memory condition to Home Assistant."""

        self.async_write_ha_state()
