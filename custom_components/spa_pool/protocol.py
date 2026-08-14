"""Balboa-compatible framing, decoding, and command construction.

This module is independent of Home Assistant and network transport. It accepts
arbitrary TCP chunks, reconstructs complete ``0x7E``-delimited frames, validates
their CRC, decodes messages supported by the transparent bridge, and builds
commands known to work with the existing spa installation.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import time
from enum import IntEnum
import logging
from typing import Final

from .models import (
    SpaBlowerState,
    SpaFault,
    SpaFilterMode,
    SpaHeatMode,
    SpaHeatState,
    SpaInitialisationMode,
    SpaLightState,
    SpaOperationalState,
    SpaProtocolUpdate,
    SpaPumpState,
    SpaReminder,
    SpaState,
    SpaTemperatureRange,
    SpaTemperatureUnit,
)

_LOGGER = logging.getLogger(__name__)

FRAME_DELIMITER: Final = 0x7E
COMMAND_PREFIX: Final = (0x0A, 0xBF)

# A Balboa length byte counts the bytes from the length byte through the CRC.
# A complete frame therefore occupies ``declared_length + 2`` bytes after the
# opening and closing delimiters are included.
_MIN_DECLARED_LENGTH: Final = 5
_MAX_BUFFER_SIZE: Final = 16_384

STATUS_MESSAGE: Final = (0xFF, 0xAF, 0x13)
READY_MESSAGE: Final = (0x10, 0xBF, 0x06)
NOTHING_TO_SEND_MESSAGE: Final = (0x10, 0xBF, 0x07)
BRIDGE_IDLE_MESSAGE: Final = (0xFE, 0xBF, 0x00)
FILTER_CYCLE_MESSAGE: Final = (0x0A, 0xBF, 0x23)
SYSTEM_INFORMATION_MESSAGE: Final = (0x0A, 0xBF, 0x24)
SETUP_PARAMETERS_MESSAGE: Final = (0x0A, 0xBF, 0x25)
FAULT_LOG_MESSAGE: Final = (0x0A, 0xBF, 0x28)
DEVICE_CONFIGURATION_MESSAGE: Final = (0x0A, 0xBF, 0x2E)
MODULE_IDENTIFICATION_MESSAGE: Final = (0x0A, 0xBF, 0x94)

_MESSAGE_NAMES: Final[dict[tuple[int, int, int], str]] = {
    STATUS_MESSAGE: "status_update",
    READY_MESSAGE: "ready_to_send",
    NOTHING_TO_SEND_MESSAGE: "nothing_to_send",
    BRIDGE_IDLE_MESSAGE: "bridge_idle",
    FILTER_CYCLE_MESSAGE: "filter_cycle",
    SYSTEM_INFORMATION_MESSAGE: "system_information",
    SETUP_PARAMETERS_MESSAGE: "setup_parameters",
    FAULT_LOG_MESSAGE: "fault_log",
    DEVICE_CONFIGURATION_MESSAGE: "device_configuration",
    MODULE_IDENTIFICATION_MESSAGE: "module_identification",
}


class SpaPoolProtocolError(Exception):
    """Raised when protocol processing cannot safely continue."""


class MessageType(IntEnum):
    """Outgoing Balboa message type."""

    DEVICE_PRESENT = 0x04
    TOGGLE_STATE = 0x11
    SET_TEMPERATURE = 0x20
    SET_TIME = 0x21
    REQUEST = 0x22
    FILTER_CYCLE = 0x23
    SET_TEMPERATURE_UNIT = 0x27


class SettingsCode(IntEnum):
    """Configuration item requested with message type ``0x22``."""

    DEVICE_CONFIGURATION = 0x00
    FILTER_CYCLE = 0x01
    SYSTEM_INFORMATION = 0x02
    SETUP_PARAMETERS = 0x04
    FAULT_LOG = 0x20


class ToggleItem(IntEnum):
    """Item codes used by the Balboa toggle-state command."""

    NORMAL_OPERATION = 0x01
    CLEAR_NOTIFICATION = 0x03

    PUMP_1 = 0x04
    PUMP_2 = 0x05
    PUMP_3 = 0x06
    PUMP_4 = 0x07
    PUMP_5 = 0x08
    PUMP_6 = 0x09

    BLOWER = 0x0C
    MISTER = 0x0E

    LIGHT_1 = 0x11
    LIGHT_2 = 0x12
    LIGHT_3 = 0x13
    LIGHT_4 = 0x14

    AUX_1 = 0x16
    AUX_2 = 0x17

    SOAK_MODE = 0x1D
    HOLD_MODE = 0x3C
    CIRCULATION_PUMP = 0x3D
    TEMPERATURE_RANGE = 0x50
    HEAT_MODE = 0x51


class SpaPoolProtocol:
    """Incrementally parse and decode Balboa-compatible protocol frames."""

    def __init__(self) -> None:
        """Initialise an empty stream buffer."""

        self._buffer = bytearray()
        self._last_state: SpaState | None = None

        self._bytes_discarded = 0
        self._valid_frame_count = 0
        self._invalid_length_count = 0
        self._invalid_checksum_count = 0
        self._invalid_payload_count = 0
        self._unknown_message_count = 0

    @property
    def last_state(self) -> SpaState | None:
        """Return the most recently decoded status state."""

        return self._last_state

    def reset_stream(self) -> None:
        """Discard an incomplete TCP fragment while retaining decoded state."""

        self._buffer.clear()

    def feed_data(self, data: bytes) -> list[SpaProtocolUpdate]:
        """Consume arbitrary TCP data and return complete valid messages."""

        if not data:
            return []

        self._buffer.extend(data)
        if len(self._buffer) > _MAX_BUFFER_SIZE:
            self._buffer.clear()
            raise SpaPoolProtocolError(
                "Spa protocol buffer exceeded its safety limit"
            )

        updates: list[SpaProtocolUpdate] = []

        while True:
            start = self._buffer.find(FRAME_DELIMITER)
            if start < 0:
                self._bytes_discarded += len(self._buffer)
                self._buffer.clear()
                break

            if start:
                self._bytes_discarded += start
                del self._buffer[:start]

            if len(self._buffer) < 2:
                break

            declared_length = self._buffer[1]
            if declared_length < _MIN_DECLARED_LENGTH:
                self._invalid_length_count += 1
                del self._buffer[0]
                continue

            frame_length = declared_length + 2
            if len(self._buffer) < frame_length:
                break

            if self._buffer[frame_length - 1] != FRAME_DELIMITER:
                self._invalid_length_count += 1
                del self._buffer[0]
                continue

            frame = bytes(self._buffer[:frame_length])
            del self._buffer[:frame_length]

            if calculate_checksum(frame[1:-2]) != frame[-2]:
                self._invalid_checksum_count += 1
                _LOGGER.debug(
                    "Discarding spa frame with invalid checksum: %s",
                    frame.hex(),
                )
                continue

            update = self._decode_frame(frame)
            if update is None:
                continue

            self._valid_frame_count += 1
            updates.append(update)

        return updates

    def _decode_frame(self, frame: bytes) -> SpaProtocolUpdate | None:
        """Decode one checksum-valid frame."""

        message_identifier = tuple(frame[2:5])
        payload = frame[5:-2]
        message_type = _MESSAGE_NAMES.get(message_identifier, "unknown")

        if message_type == "status_update":
            state = self._decode_status(payload, frame)
            if state is None:
                return None
            self._last_state = state
            return SpaProtocolUpdate(
                raw_frame=frame,
                message_type=message_type,
                payload=payload,
                state=state,
            )

        if message_type == "fault_log":
            fault = self._decode_fault(payload)
            if fault is None:
                return None

            state = self._last_state
            if state is not None:
                state = replace(state, last_fault=fault)
                self._last_state = state

            return SpaProtocolUpdate(
                raw_frame=frame,
                message_type=message_type,
                payload=payload,
                state=state,
                fault=fault,
            )

        if message_type == "unknown":
            self._unknown_message_count += 1

        # Handshake and configuration messages remain available to the client
        # even when no higher-level decoder has been added for their payload.
        return SpaProtocolUpdate(
            raw_frame=frame,
            message_type=message_type,
            payload=payload,
            state=None,
        )

    def _decode_status(
        self,
        payload: bytes,
        raw_frame: bytes,
    ) -> SpaState | None:
        """Decode the 24-byte ``FF AF 13`` status payload."""

        if len(payload) < 23:
            self._invalid_payload_count += 1
            _LOGGER.warning(
                "Discarding short spa status payload (%s bytes)",
                len(payload),
            )
            return None

        flags_3 = payload[9]
        flags_4 = payload[10]
        flags_7 = payload[21]

        temperature_unit = (
            SpaTemperatureUnit.CELSIUS
            if flags_3 & 0x01
            else SpaTemperatureUnit.FAHRENHEIT
        )
        divisor = 2 if temperature_unit is SpaTemperatureUnit.CELSIUS else 1

        current_temperature = (
            None if payload[2] == 0xFF else payload[2] / divisor
        )
        target_temperature = payload[20] / divisor

        operational_state = SpaOperationalState(payload[0])
        initialisation_mode = SpaInitialisationMode(payload[1])
        heat_mode = SpaHeatMode(payload[5] & 0x03)

        sensor_ab_temperatures = bool(flags_7 & 0x02)
        hold_mode = operational_state is SpaOperationalState.HOLD_MODE
        test_mode = operational_state is SpaOperationalState.TEST_MODE

        sensor_a_temperature: float | None = None
        sensor_b_temperature: float | None = None
        hold_timer_minutes: int | None = None
        test_mode_value: int | None = None

        if sensor_ab_temperatures:
            sensor_a_temperature = _decode_optional_temperature(
                payload[7], divisor
            )
            sensor_b_temperature = _decode_optional_temperature(
                payload[8], divisor
            )
        elif hold_mode:
            hold_timer_minutes = payload[7]
        elif test_mode:
            test_mode_value = payload[7]

        pumps = tuple(
            SpaPumpState((payload[11 + byte_index] >> (2 * item)) & 0x03)
            for byte_index in range(2)
            for item in range(4)
        )

        blowers = tuple(
            SpaBlowerState((payload[13] >> shift) & 0x03)
            for shift in (2, 4)
        )

        # The controller exposes only inactive/active status for the light
        # circuits. Colour and effect selection remain internal to the light
        # controller and are advanced by repeated toggle commands.
        lights = tuple(
            SpaLightState.ON
            if ((payload[14] >> (2 * item)) & 0x03)
            else SpaLightState.OFF
            for item in range(4)
        )

        previous_fault = (
            self._last_state.last_fault
            if self._last_state is not None
            else None
        )

        return SpaState(
            operational_state=operational_state,
            initialisation_mode=initialisation_mode,
            current_temperature=current_temperature,
            target_temperature=target_temperature,
            spa_time=_safe_time(payload[3], payload[4]),
            clock_24_hour=bool(flags_3 & 0x02),
            heat_mode=heat_mode,
            reminder=SpaReminder.from_code(payload[6]),
            temperature_unit=temperature_unit,
            filter_mode=SpaFilterMode((flags_3 >> 2) & 0x03),
            panel_locked=bool(flags_3 & 0x20),
            temperature_range=SpaTemperatureRange((flags_4 >> 2) & 0x01),
            heat_state=SpaHeatState((flags_4 >> 4) & 0x03),
            pumps=pumps,
            circulation_pump=bool(payload[13] & 0x02),
            blowers=blowers,
            lights=lights,
            misters=tuple(
                bool((payload[15] >> item) & 0x01)
                for item in range(3)
            ),
            auxiliaries=tuple(
                bool((payload[15] >> (item + 3)) & 0x01)
                for item in range(4)
            ),
            sensor_ab_temperatures=sensor_ab_temperatures,
            sensor_a_temperature=sensor_a_temperature,
            sensor_b_temperature=sensor_b_temperature,
            hold_timer_minutes=hold_timer_minutes,
            test_mode_value=test_mode_value,
            timeouts_active=bool(flags_7 & 0x04),
            settings_locked=bool(flags_7 & 0x08),
            wifi_state=(payload[22] >> 4) & 0x0F,
            raw_status_payload=bytes(payload),
            raw_status_frame=raw_frame,
            last_fault=previous_fault,
        )

    def _decode_fault(self, payload: bytes) -> SpaFault | None:
        """Decode a 10-byte Balboa fault-log response."""

        if len(payload) < 10:
            self._invalid_payload_count += 1
            _LOGGER.warning(
                "Discarding short spa fault payload (%s bytes)",
                len(payload),
            )
            return None

        unit = (
            self._last_state.temperature_unit
            if self._last_state is not None
            else SpaTemperatureUnit.UNKNOWN
        )
        divisor = 2 if unit is SpaTemperatureUnit.CELSIUS else 1

        return SpaFault(
            count=payload[0],
            entry_number=payload[1],
            message_code=payload[2],
            days_ago=payload[3],
            spa_time=_safe_time(payload[4], payload[5]),
            flags=payload[6],
            target_temperature=_decode_optional_temperature(
                payload[7], divisor
            ),
            sensor_a_temperature=_decode_optional_temperature(
                payload[8], divisor
            ),
            sensor_b_temperature=_decode_optional_temperature(
                payload[9], divisor
            ),
            temperature_unit=unit,
            raw_payload=bytes(payload),
        )

    def diagnostics(self) -> dict[str, int]:
        """Return parser counters suitable for integration diagnostics."""

        return {
            "buffered_bytes": len(self._buffer),
            "bytes_discarded": self._bytes_discarded,
            "valid_frames": self._valid_frame_count,
            "invalid_lengths": self._invalid_length_count,
            "invalid_checksums": self._invalid_checksum_count,
            "invalid_payloads": self._invalid_payload_count,
            "unknown_messages": self._unknown_message_count,
        }


def calculate_checksum(data: bytes) -> int:
    """Calculate the Balboa CRC-8 checksum."""

    crc = 0xB5

    for current_byte in data:
        for bit_index in range(8):
            high_bit = crc & 0x80
            crc = (
                ((crc << 1) & 0xFF)
                | ((current_byte >> (7 - bit_index)) & 0x01)
            )
            if high_bit:
                crc ^= 0x07

        crc &= 0xFF

    for _ in range(8):
        high_bit = crc & 0x80
        crc = (crc << 1) & 0xFF
        if high_bit:
            crc ^= 0x07

    return crc ^ 0x02


def build_message(
    message_type: MessageType | int,
    *payload: int,
) -> bytes:
    """Build one complete command frame with CRC and delimiters."""

    values = (
        *COMMAND_PREFIX,
        _validate_byte(int(message_type), "message type"),
        *(_validate_byte(value, "payload byte") for value in payload),
    )

    declared_length = len(values) + 2
    body = bytes((declared_length, *values))
    checksum = calculate_checksum(body)

    return bytes((FRAME_DELIMITER,)) + body + bytes(
        (checksum, FRAME_DELIMITER)
    )


def build_toggle_command(
    item: ToggleItem | int,
    *,
    trailing_zero: bool = True,
) -> bytes:
    """Build a spa-panel toggle/button command."""

    payload = (int(item), 0x00) if trailing_zero else (int(item),)
    return build_message(MessageType.TOGGLE_STATE, *payload)


def build_set_temperature_command(
    temperature: float,
    unit: SpaTemperatureUnit,
) -> bytes:
    """Build a target-temperature command in the active spa unit."""

    if unit is SpaTemperatureUnit.CELSIUS:
        encoded = round(temperature * 2)
        if abs((encoded / 2) - temperature) > 1e-9:
            raise ValueError(
                "Celsius setpoints must use 0.5 degree increments"
            )
    elif unit is SpaTemperatureUnit.FAHRENHEIT:
        encoded = round(temperature)
        if abs(encoded - temperature) > 1e-9:
            raise ValueError(
                "Fahrenheit setpoints must use whole-degree increments"
            )
    else:
        raise ValueError("Cannot encode a temperature with an unknown unit")

    return build_message(MessageType.SET_TEMPERATURE, encoded)


def build_set_time_command(
    hour: int,
    minute: int,
    *,
    clock_24_hour: bool,
) -> bytes:
    """Build a spa-clock command."""

    if not 0 <= hour <= 23:
        raise ValueError("Hour must be between 0 and 23")
    if not 0 <= minute <= 59:
        raise ValueError("Minute must be between 0 and 59")

    encoded_hour = hour | (0x80 if clock_24_hour else 0x00)
    return build_message(MessageType.SET_TIME, encoded_hour, minute)


def build_temperature_unit_command(
    unit: SpaTemperatureUnit,
) -> bytes:
    """Build a temperature-unit command."""

    if unit not in (
        SpaTemperatureUnit.FAHRENHEIT,
        SpaTemperatureUnit.CELSIUS,
    ):
        raise ValueError("Temperature unit must be Fahrenheit or Celsius")

    return build_message(
        MessageType.SET_TEMPERATURE_UNIT,
        0x01,
        int(unit),
    )


def build_request_command(
    setting: SettingsCode | int,
    first_argument: int = 0x00,
    second_argument: int = 0x00,
) -> bytes:
    """Build a Balboa configuration request."""

    return build_message(
        MessageType.REQUEST,
        int(setting),
        first_argument,
        second_argument,
    )


def build_fault_log_request(entry: int = 0xFF) -> bytes:
    """Request one fault entry, or the latest entry with ``0xFF``."""

    if entry != 0xFF and not 0 <= entry < 24:
        raise ValueError(
            "Fault entry must be 0-23 or 0xFF for the latest entry"
        )

    return build_request_command(
        SettingsCode.FAULT_LOG,
        entry,
        0x00,
    )


def build_device_present_command() -> bytes:
    """Build the module-identification/configuration request ``0A BF 04``."""

    return build_message(MessageType.DEVICE_PRESENT)


def _decode_optional_temperature(
    raw_value: int,
    divisor: int,
) -> float | None:
    """Decode a temperature byte, preserving ``0xFF`` as unavailable."""

    return None if raw_value == 0xFF else raw_value / divisor


def _safe_time(hour: int, minute: int) -> time | None:
    """Return a time when the spa-provided values are valid."""

    try:
        return time(hour=hour, minute=minute)
    except ValueError:
        return None


def _validate_byte(value: int, label: str) -> int:
    """Return a validated unsigned byte."""

    if not 0 <= value <= 0xFF:
        raise ValueError(f"{label} must be between 0 and 255")
    return value
