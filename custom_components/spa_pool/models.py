"""Typed protocol models for the Spa Pool integration.

The models in this module are independent of Home Assistant. Unknown numeric
values are retained rather than rejected so that undocumented spa firmware
codes cannot terminate the TCP stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from enum import IntEnum
from typing import Any, ClassVar


class SpaIntEnum(IntEnum):
    """Integer enum that preserves undocumented protocol values."""

    @classmethod
    def _missing_(cls, value: object) -> SpaIntEnum | None:
        """Create a stable pseudo-member for an unknown integer value."""

        if not isinstance(value, int):
            return None

        member = int.__new__(cls, value)
        member._name_ = f"UNKNOWN_0x{value:02X}"
        member._value_ = value
        cls._value2member_map_[value] = member
        return member

    @property
    def is_known(self) -> bool:
        """Return whether this value was explicitly defined."""

        return not self.name.startswith("UNKNOWN_")

    @property
    def label(self) -> str:
        """Return a human-readable label without losing unknown values."""

        if not self.is_known:
            width = max(2, (int(self).bit_length() + 3) // 4)
            return f"Unknown (0x{int(self):0{width}X})"

        return self.name.replace("_", " ").title()

    def __str__(self) -> str:
        """Return the human-readable representation."""

        return self.label


class SpaOperationalState(SpaIntEnum):
    """Controller state stored in status payload byte 0."""

    RUNNING = 0x00
    INITIALISING = 0x01
    HOLD_MODE = 0x05
    SENSOR_AB_TEMPERATURES = 0x14
    TEST_MODE = 0x17


class SpaInitialisationMode(SpaIntEnum):
    """Initialisation or notification state stored in payload byte 1."""

    IDLE = 0x00
    PRIMING_MODE = 0x01
    POST_SETTINGS_RESET = 0x02
    REMINDER = 0x03
    STAGE_1 = 0x04
    STAGE_3 = 0x05
    STAGE_2 = 0x42


class SpaTemperatureUnit(SpaIntEnum):
    """Temperature scale encoded by the spa."""

    UNKNOWN = -1
    FAHRENHEIT = 0
    CELSIUS = 1

    @property
    def symbol(self) -> str | None:
        """Return the unit symbol suitable for display."""

        if self is SpaTemperatureUnit.CELSIUS:
            return "°C"
        if self is SpaTemperatureUnit.FAHRENHEIT:
            return "°F"
        return None


class SpaHeatMode(SpaIntEnum):
    """Requested heating mode."""

    READY = 0
    REST = 1
    READY_IN_REST = 3


class SpaHeatState(SpaIntEnum):
    """Current heater activity."""

    OFF = 0
    HEATING = 1
    HEAT_WAITING = 2
    UNKNOWN_STATE = 3


class SpaFilterMode(SpaIntEnum):
    """Active filter-cycle flags."""

    OFF = 0
    CYCLE_1 = 1
    CYCLE_2 = 2
    CYCLE_1_AND_2 = 3


class SpaTemperatureRange(SpaIntEnum):
    """Selected Balboa temperature range."""

    LOW = 0
    HIGH = 1


class SpaPumpState(SpaIntEnum):
    """Pump speed encoded in a two-bit field."""

    OFF = 0
    LOW = 1
    HIGH = 2
    UNKNOWN_STATE = 3

    @property
    def is_on(self) -> bool:
        """Return whether the pump is running at a recognised speed."""

        return self in (SpaPumpState.LOW, SpaPumpState.HIGH)


class SpaBlowerState(SpaIntEnum):
    """Blower speed encoded in a two-bit field."""

    OFF = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    @property
    def is_on(self) -> bool:
        """Return whether the blower is running."""

        return self is not SpaBlowerState.OFF


class SpaLightState(SpaIntEnum):
    """Normalised light state."""

    OFF = 0
    ON = 1

    @property
    def is_on(self) -> bool:
        """Return whether the light is on."""

        return self is SpaLightState.ON


_NOTIFICATION_DESCRIPTIONS: dict[int, str] = {
    0x00: "None",
    0x04: "Clean filter",
    0x09: "Check sanitizer",
    0x0A: "Check pH",
    0x0F: "Sensors are out of sync",
    0x10: "Water flow is low",
    0x11: "Water flow has failed",
    0x12: "Settings have been reset",
    0x13: "Priming mode",
    0x14: "Clock failure",
    0x15: "Settings have been reset",
    0x16: "Program memory failure",
    0x1A: "Sensors are out of sync; service required",
    0x1B: "Heater is dry",
    0x1C: "Heater may be dry",
    0x1D: "Water is too hot",
    0x1E: "Heater is too hot",
    0x1F: "Sensor A fault",
    0x20: "Sensor B fault",
    0x22: "Pump may be stuck on",
    0x23: "Hot fault",
    0x24: "GFCI/RCD test failed",
    0x25: "Hold or standby mode",
}

_CRITICAL_NOTIFICATION_CODES = frozenset({0x1B, 0x1D, 0x1E, 0x22, 0x23})
_MAINTENANCE_NOTIFICATION_CODES = frozenset({0x04, 0x09, 0x0A})


@dataclass(frozen=True, slots=True)
class SpaReminder:
    """Controller notification carried in the regular status message.

    The field contains both scheduled maintenance reminders and live controller
    warnings/faults, despite historically being described as a reminder byte.
    """

    code: int
    description: str
    known: bool = True

    @classmethod
    def from_code(cls, code: int) -> SpaReminder:
        """Decode a controller notification while preserving unknown codes."""

        normalised_code = int(code) & 0xFF
        description = _NOTIFICATION_DESCRIPTIONS.get(normalised_code)

        if description is None:
            return cls(
                code=normalised_code,
                description=f"Unknown controller notification 0x{normalised_code:02X}",
                known=False,
            )

        return cls(
            code=normalised_code,
            description=description,
            known=True,
        )

    @property
    def active(self) -> bool:
        """Return whether the spa is reporting a notification."""

        return self.code != 0

    @property
    def is_maintenance(self) -> bool:
        """Return whether this is a scheduled maintenance reminder."""

        return self.code in _MAINTENANCE_NOTIFICATION_CODES

    @property
    def is_fault(self) -> bool:
        """Return whether this is a recognised controller warning or fault."""

        return self.active and self.known and not self.is_maintenance

    @property
    def critical(self) -> bool:
        """Return whether this notification warrants prominent attention."""

        return self.code in _CRITICAL_NOTIFICATION_CODES

    @property
    def category(self) -> str:
        """Return a stable category for Home Assistant attributes."""

        if not self.active:
            return "none"
        if not self.known:
            return "unknown"
        if self.is_maintenance:
            return "maintenance"
        return "fault"

    def __str__(self) -> str:
        """Return the display description."""

        return self.description


@dataclass(frozen=True, slots=True)
class SpaFault:
    """One entry returned from the controller fault log."""

    count: int
    entry_number: int
    message_code: int
    days_ago: int
    spa_time: time | None
    flags: int
    target_temperature: float | None
    sensor_a_temperature: float | None
    sensor_b_temperature: float | None
    temperature_unit: SpaTemperatureUnit
    raw_payload: bytes

    @property
    def description(self) -> str:
        """Return a known description or retain the raw fault code."""

        return _NOTIFICATION_DESCRIPTIONS.get(
            self.message_code,
            f"Unknown fault code {self.message_code}",
        )

    @property
    def known(self) -> bool:
        """Return whether the fault code has a description."""

        return self.message_code in _NOTIFICATION_DESCRIPTIONS

    @property
    def critical(self) -> bool:
        """Return whether the code warrants prominent notification."""

        return self.message_code in _CRITICAL_NOTIFICATION_CODES

    @property
    def active(self) -> bool:
        """Return whether this entry represents a fault."""

        return self.message_code != 0

    def as_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """Return serialisable fault information for events and diagnostics."""

        data: dict[str, Any] = {
            "count": self.count,
            "entry_number": self.entry_number,
            "message_code": self.message_code,
            "description": self.description,
            "known": self.known,
            "critical": self.critical,
            "days_ago": self.days_ago,
            "spa_time": (
                self.spa_time.isoformat(timespec="minutes")
                if self.spa_time is not None
                else None
            ),
            "flags": self.flags,
            "target_temperature": self.target_temperature,
            "sensor_a_temperature": self.sensor_a_temperature,
            "sensor_b_temperature": self.sensor_b_temperature,
            "temperature_unit": self.temperature_unit.name.lower(),
        }

        if include_raw:
            data["raw_payload"] = self.raw_payload.hex()

        return data


@dataclass(frozen=True, slots=True)
class SpaState:
    """Complete state decoded from the latest regular status message."""

    operational_state: SpaOperationalState
    initialisation_mode: SpaInitialisationMode

    current_temperature: float | None
    target_temperature: float
    spa_time: time | None
    clock_24_hour: bool

    heat_mode: SpaHeatMode
    reminder: SpaReminder
    temperature_unit: SpaTemperatureUnit
    filter_mode: SpaFilterMode
    panel_locked: bool
    temperature_range: SpaTemperatureRange
    heat_state: SpaHeatState

    pumps: tuple[SpaPumpState, ...]
    circulation_pump: bool
    blowers: tuple[SpaBlowerState, ...]
    lights: tuple[SpaLightState, ...]
    misters: tuple[bool, ...]
    auxiliaries: tuple[bool, ...]

    sensor_ab_temperatures: bool
    sensor_a_temperature: float | None
    sensor_b_temperature: float | None
    hold_timer_minutes: int | None
    test_mode_value: int | None

    timeouts_active: bool
    settings_locked: bool
    wifi_state: int

    raw_status_payload: bytes
    raw_status_frame: bytes
    last_fault: SpaFault | None = None

    @property
    def is_heating(self) -> bool:
        """Return whether the heater is actively heating."""

        return self.heat_state is SpaHeatState.HEATING

    @property
    def is_ready(self) -> bool:
        """Return whether the spa is in Ready or Ready-in-Rest mode."""

        return self.heat_mode in (
            SpaHeatMode.READY,
            SpaHeatMode.READY_IN_REST,
        )

    @property
    def has_active_reminder(self) -> bool:
        """Return whether a controller notification is active."""

        return self.reminder.active

    def pump(self, number: int) -> SpaPumpState | None:
        """Return a one-based pump state, or ``None`` when unavailable."""

        if number < 1 or number > len(self.pumps):
            return None
        return self.pumps[number - 1]

    def light(self, number: int) -> SpaLightState | None:
        """Return a one-based light state, or ``None`` when unavailable."""

        if number < 1 or number > len(self.lights):
            return None
        return self.lights[number - 1]

    def as_dict(self, *, include_raw: bool = False) -> dict[str, Any]:
        """Return serialisable state information for diagnostics."""

        data: dict[str, Any] = {
            "operational_state": self.operational_state.label,
            "operational_state_code": int(self.operational_state),
            "initialisation_mode": self.initialisation_mode.label,
            "initialisation_mode_code": int(self.initialisation_mode),
            "current_temperature": self.current_temperature,
            "target_temperature": self.target_temperature,
            "spa_time": (
                self.spa_time.isoformat(timespec="minutes")
                if self.spa_time is not None
                else None
            ),
            "clock_24_hour": self.clock_24_hour,
            "heat_mode": self.heat_mode.label,
            "heat_mode_code": int(self.heat_mode),
            "heat_state": self.heat_state.label,
            "heat_state_code": int(self.heat_state),
            "temperature_unit": self.temperature_unit.name.lower(),
            "temperature_range": self.temperature_range.label,
            "filter_mode": self.filter_mode.label,
            "panel_locked": self.panel_locked,
            "settings_locked": self.settings_locked,
            "timeouts_active": self.timeouts_active,
            "reminder": self.reminder.description,
            "reminder_code": self.reminder.code,
            "reminder_category": self.reminder.category,
            "reminder_critical": self.reminder.critical,
            "pumps": [pump.label for pump in self.pumps],
            "circulation_pump": self.circulation_pump,
            "blowers": [blower.label for blower in self.blowers],
            "lights": [light.label for light in self.lights],
            "misters": list(self.misters),
            "auxiliaries": list(self.auxiliaries),
            "sensor_ab_temperatures": self.sensor_ab_tematures
            if False
            else self.sensor_ab_temperatures,
            "sensor_a_temperature": self.sensor_a_temperature,
            "sensor_b_temperature": self.sensor_b_temperature,
            "hold_timer_minutes": self.hold_timer_minutes,
            "test_mode_value": self.test_mode_value,
            "wifi_state": self.wifi_state,
            "last_fault": (
                self.last_fault.as_dict(include_raw=include_raw)
                if self.last_fault is not None
                else None
            ),
        }

        if include_raw:
            data["raw_status_payload"] = self.raw_status_payload.hex()
            data["raw_status_frame"] = self.raw_status_frame.hex()

        return data


@dataclass(frozen=True, slots=True)
class SpaProtocolUpdate:
    """One checksum-valid message emitted by the protocol parser."""

    raw_frame: bytes
    message_type: str
    payload: bytes
    state: SpaState | None = None
    fault: SpaFault | None = None
