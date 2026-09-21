"""Event platform for spa controller fault-log entries."""

from __future__ import annotations

from typing import Final, override

from homeassistant.components.event import EventEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SpaPoolConfigEntry
from .const import DOMAIN
from .models import SpaFault

_EVENT_FAULT: Final = "fault"
_EVENT_CRITICAL_FAULT: Final = "critical_fault"
_EVENT_UNKNOWN_FAULT: Final = "unknown_fault"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the spa fault-log event entity."""

    async_add_entities([SpaPoolFaultEventEntity(entry)])


class SpaPoolFaultEventEntity(EventEntity):
    """Emit an event when the controller returns a new fault-log entry."""

    _attr_has_entity_name = True
    _attr_translation_key = "fault_log"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:alert-circle-outline"
    _attr_event_types = [
        _EVENT_FAULT,
        _EVENT_CRITICAL_FAULT,
        _EVENT_UNKNOWN_FAULT,
    ]

    def __init__(self, entry: SpaPoolConfigEntry) -> None:
        """Initialise the fault event entity."""

        self._entry = entry
        self._client = entry.runtime_data.client
        self._last_fault_key: tuple[object, ...] | None = None

        self._attr_unique_id = f"{entry.entry_id}_fault_log"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Balboa-compatible",
            model="RS-485 spa controller",
        )

    @property
    @override
    def available(self) -> bool:
        """Return whether the spa status stream is available."""

        return self._client.transport_available

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to decoded fault-log messages."""

        await super().async_added_to_hass()
        self.async_on_remove(
            self._client.async_add_fault_listener(
                self._handle_fault,
            )
        )

    @callback
    def _handle_fault(self, fault: SpaFault) -> None:
        """Emit one event for a distinct non-zero fault-log entry."""

        if not fault.active:
            return

        fault_key = (
            fault.count,
            fault.entry_number,
            fault.message_code,
            fault.spa_time,
            fault.flags,
            fault.target_temperature,
            fault.sensor_a_temperature,
            fault.sensor_b_temperature,
        )
        if fault_key == self._last_fault_key:
            return

        self._last_fault_key = fault_key

        if not fault.known:
            event_type = _EVENT_UNKNOWN_FAULT
        elif fault.critical:
            event_type = _EVENT_CRITICAL_FAULT
        else:
            event_type = _EVENT_FAULT

        self._trigger_event(
            event_type,
            fault.as_dict(include_raw=True),
        )
        self.async_write_ha_state()
