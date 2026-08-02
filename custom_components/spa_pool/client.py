"""Persistent TCP client for a Balboa-compatible spa bridge.

The bridge is treated as a transparent RS-485-to-TCP transport. The client
does not require the identification or configuration handshake implemented by
the official Balboa Wi-Fi adapter.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
import logging
from typing import Any

from .models import SpaFault, SpaState
from .protocol import SpaPoolProtocol, SpaPoolProtocolError

_LOGGER = logging.getLogger(__name__)

# Balboa status traffic is normally frequent. A long silence indicates either
# a stalled TCP bridge or a connection that should be rebuilt.
_CONNECT_TIMEOUT = 10.0
_FIRST_FRAME_TIMEOUT = 20.0
_STREAM_STALE_TIMEOUT = 15.0
_WRITE_TIMEOUT = 5.0
_COMMAND_CONNECTION_TIMEOUT = 10.0
_CLOSE_TIMEOUT = 5.0
_READ_SIZE = 4096

_RECONNECT_INITIAL_DELAY = 1.0
_RECONNECT_MAX_DELAY = 60.0

Listener = Callable[[], None]
FaultListener = Callable[[SpaFault], None]
StatePredicate = Callable[[SpaState], bool]


class SpaPoolError(Exception):
    """Base exception for the Spa Pool client."""


class SpaPoolConnectionError(SpaPoolError):
    """Raised when the spa bridge cannot provide a valid connection."""


class SpaPoolNotConnectedError(SpaPoolConnectionError):
    """Raised when a command cannot be sent because the stream is unavailable."""


class SpaPoolCommandError(SpaPoolError):
    """Raised when a command cannot be written or confirmed."""


class SpaPoolClient:
    """Maintain a self-healing TCP stream to one spa bridge."""

    def __init__(
        self,
        host: str,
        port: int,
        *,
        protocol: SpaPoolProtocol | None = None,
    ) -> None:
        """Initialise the client without opening a network connection."""

        self.host = host
        self.port = port
        self._protocol = protocol or SpaPoolProtocol()

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._runner_task: asyncio.Task[None] | None = None

        self._lifecycle_lock = asyncio.Lock()

        # _write_lock protects the socket write itself. _command_lock protects
        # the complete protocol transaction: queue write -> confirmation.
        # The transparent TCP bridge accepts commands immediately and places
        # them into a later RS-485 transmission slot itself.
        self._write_lock = asyncio.Lock()
        self._command_lock = asyncio.Lock()

        self._state_condition = asyncio.Condition()
        self._fault_condition = asyncio.Condition()
        self._device_configuration_condition = asyncio.Condition()

        self._first_frame_event = asyncio.Event()
        self._available_event = asyncio.Event()

        self._listeners: set[Listener] = set()
        self._message_listeners: set[Listener] = set()
        self._fault_listeners: set[FaultListener] = set()

        self._state: SpaState | None = None
        self._state_revision = 0
        self._last_fault: SpaFault | None = None
        self._fault_revision = 0

        # Retain the latest controller capability/configuration response.
        # These frames are otherwise immediately displaced by frequent status
        # and bus-traffic messages in ``last_frame``.
        self._device_configuration_frame: bytes | None = None
        self._device_configuration_payload: bytes | None = None
        self._device_configuration_at: datetime | None = None
        self._device_configuration_revision = 0

        self._ready_revision = 0

        self._connected = False
        self._available = False
        self._current_connection_received_frame = False

        self._last_frame: bytes | None = None
        self._last_message_type: str | None = None
        self._last_message_at: datetime | None = None
        self._last_error: str | None = None

        self._connection_attempts = 0
        self._reconnect_count = 0
        self._valid_frame_count = 0
        self._valid_status_count = 0
        self._ready_to_send_count = 0
        self._nothing_to_send_count = 0
        self._bridge_idle_count = 0
        self._protocol_error_count = 0

    @property
    def state(self) -> SpaState | None:
        """Return the most recently decoded spa state."""

        return self._state

    @property
    def state_revision(self) -> int:
        """Return the number of decoded regular status messages received."""

        return self._state_revision

    @property
    def last_fault(self) -> SpaFault | None:
        """Return the most recently decoded fault-log entry."""

        return self._last_fault

    @property
    def fault_revision(self) -> int:
        """Return the number of decoded fault-log entries received."""

        return self._fault_revision

    @property
    def device_configuration_revision(self) -> int:
        """Return the number of device-configuration frames received."""

        return self._device_configuration_revision

    @property
    def device_configuration_frame(self) -> bytes | None:
        """Return the latest complete device-configuration frame."""

        return self._device_configuration_frame

    @property
    def device_configuration_payload(self) -> bytes | None:
        """Return the latest device-configuration payload."""

        return self._device_configuration_payload

    @property
    def device_configuration_at(self) -> datetime | None:
        """Return when the latest device configuration was received."""

        return self._device_configuration_at

    @property
    def connected(self) -> bool:
        """Return whether a TCP socket is currently open."""

        return self._connected

    @property
    def available(self) -> bool:
        """Return whether the current connection has supplied a valid frame."""

        return self._available

    @property
    def last_message_at(self) -> datetime | None:
        """Return the UTC time of the most recent valid protocol frame."""

        return self._last_message_at

    @property
    def last_frame(self) -> bytes | None:
        """Return the most recent valid raw frame."""

        return self._last_frame

    def async_add_listener(self, listener: Listener) -> Callable[[], None]:
        """Subscribe to state or availability changes."""

        self._listeners.add(listener)

        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    def async_add_message_listener(
        self,
        listener: Listener,
    ) -> Callable[[], None]:
        """Subscribe to every checksum-valid protocol frame."""

        self._message_listeners.add(listener)

        def remove_listener() -> None:
            self._message_listeners.discard(listener)

        return remove_listener

    def async_add_fault_listener(
        self,
        listener: FaultListener,
    ) -> Callable[[], None]:
        """Subscribe to decoded fault-log entries."""

        self._fault_listeners.add(listener)

        def remove_listener() -> None:
            self._fault_listeners.discard(listener)

        return remove_listener

    async def async_start(self) -> None:
        """Start the reader and wait until at least one valid frame arrives."""

        async with self._lifecycle_lock:
            if self._runner_task is None or self._runner_task.done():
                self._first_frame_event.clear()
                self._runner_task = asyncio.create_task(
                    self._async_run(),
                    name=f"spa_pool_reader_{self.host}_{self.port}",
                )

        try:
            async with asyncio.timeout(_FIRST_FRAME_TIMEOUT):
                await self._first_frame_event.wait()
        except TimeoutError as err:
            await self.async_stop()
            raise SpaPoolConnectionError(
                f"No valid spa frame received from {self.host}:{self.port}"
            ) from err

    async def async_stop(self) -> None:
        """Stop reconnecting and close the current TCP connection."""

        async with self._lifecycle_lock:
            task = self._runner_task
            self._runner_task = None

            if task is not None and not task.done():
                task.cancel()

        if task is not None:
            with suppress(asyncio.CancelledError):
                await task

        await self._async_close_connection()
        self._set_available(False)

    async def async_reconnect(self) -> None:
        """Manually rebuild the stream without restarting Home Assistant."""

        _LOGGER.info(
            "Restarting spa stream for %s:%s",
            self.host,
            self.port,
        )
        await self.async_stop()
        await self.async_start()

    async def async_send_frame(self, frame: bytes) -> None:
        """Queue one command on the transparent TCP bridge.

        The bridge was verified from the command line to accept a complete
        Balboa command immediately, then transmit it during a later bus slot.
        Waiting for an observed ``10 BF 06`` frame before writing is therefore
        both unnecessary and potentially too late.
        """

        async with self._command_lock:
            await self._async_wait_until_available()
            await self._async_write_frame(frame)

    async def async_send_and_wait(
        self,
        frame: bytes,
        predicate: StatePredicate,
        *,
        timeout: float = 10.0,
    ) -> SpaState:
        """Queue a command and confirm it from a newer status message."""

        async with self._command_lock:
            await self._async_wait_until_available()
            revision = self._state_revision
            await self._async_write_frame(frame)

            try:
                return await self.async_wait_for_state(
                    predicate,
                    after_revision=revision,
                    timeout=timeout,
                )
            except TimeoutError as err:
                raise SpaPoolCommandError(
                    "The spa did not confirm the requested state"
                ) from err

    async def async_send_and_wait_for_fault(
        self,
        frame: bytes,
        *,
        timeout: float = 10.0,
    ) -> SpaFault:
        """Queue a fault-log request and await a newer fault entry."""

        async with self._command_lock:
            await self._async_wait_until_available()
            revision = self._fault_revision
            await self._async_write_frame(frame)

            try:
                return await self.async_wait_for_fault(
                    after_revision=revision,
                    timeout=timeout,
                )
            except TimeoutError as err:
                raise SpaPoolCommandError(
                    "The spa did not return a fault-log entry"
                ) from err

    async def async_send_and_wait_for_device_configuration(
        self,
        frame: bytes,
        *,
        timeout: float = 10.0,
    ) -> tuple[bytes, bytes]:
        """Queue the panel request and await a newer ``0A BF 2E`` frame."""

        async with self._command_lock:
            await self._async_wait_until_available()
            revision = self._device_configuration_revision
            await self._async_write_frame(frame)

            try:
                return await self.async_wait_for_device_configuration(
                    after_revision=revision,
                    timeout=timeout,
                )
            except TimeoutError as err:
                raise SpaPoolCommandError(
                    "The spa did not return a device-configuration message"
                ) from err

    async def async_wait_for_device_configuration(
        self,
        *,
        after_revision: int | None = None,
        timeout: float = 10.0,
    ) -> tuple[bytes, bytes]:
        """Wait for a newer retained device-configuration response."""

        minimum_revision = (
            self._device_configuration_revision
            if after_revision is None
            else after_revision
        )

        async with asyncio.timeout(timeout):
            async with self._device_configuration_condition:
                while True:
                    frame = self._device_configuration_frame
                    payload = self._device_configuration_payload
                    if (
                        self._device_configuration_revision > minimum_revision
                        and frame is not None
                        and payload is not None
                    ):
                        return frame, payload

                    await self._device_configuration_condition.wait()

    async def async_wait_for_state(
        self,
        predicate: StatePredicate,
        *,
        after_revision: int | None = None,
        timeout: float = 10.0,
    ) -> SpaState:
        """Wait for a newer regular status message satisfying a predicate."""

        minimum_revision = (
            self._state_revision if after_revision is None else after_revision
        )

        async with asyncio.timeout(timeout):
            async with self._state_condition:
                while True:
                    state = self._state
                    if (
                        self._state_revision > minimum_revision
                        and state is not None
                        and predicate(state)
                    ):
                        return state

                    await self._state_condition.wait()

    async def async_wait_for_fault(
        self,
        *,
        after_revision: int | None = None,
        timeout: float = 10.0,
    ) -> SpaFault:
        """Wait for a newer decoded fault-log entry."""

        minimum_revision = (
            self._fault_revision if after_revision is None else after_revision
        )

        async with asyncio.timeout(timeout):
            async with self._fault_condition:
                while True:
                    fault = self._last_fault
                    if (
                        self._fault_revision > minimum_revision
                        and fault is not None
                    ):
                        return fault

                    await self._fault_condition.wait()

    async def _async_wait_until_available(self) -> None:
        """Wait briefly for a usable status stream."""

        try:
            async with asyncio.timeout(_COMMAND_CONNECTION_TIMEOUT):
                await self._available_event.wait()
        except TimeoutError as err:
            raise SpaPoolNotConnectedError(
                f"Spa bridge {self.host}:{self.port} is unavailable"
            ) from err

    async def _async_write_frame(self, frame: bytes) -> None:
        """Write one fully framed command without acquiring command scope."""

        if not frame:
            raise ValueError("Cannot send an empty spa command")

        async with self._write_lock:
            writer = self._writer
            if writer is None or writer.is_closing():
                raise SpaPoolNotConnectedError(
                    f"Spa bridge {self.host}:{self.port} is not connected"
                )

            try:
                writer.write(frame)
                async with asyncio.timeout(_WRITE_TIMEOUT):
                    await writer.drain()
            except (ConnectionError, OSError, TimeoutError) as err:
                # Closing the writer wakes the reader loop and causes the
                # normal reconnect path to take over.
                writer.close()
                raise SpaPoolCommandError("Failed to send spa command") from err

    async def _async_run(self) -> None:
        """Connect, consume frames, and reconnect until cancelled."""

        reconnect_delay = _RECONNECT_INITIAL_DELAY
        has_connected_before = False

        while True:
            self._current_connection_received_frame = False

            try:
                await self._async_connect_and_listen()
            except asyncio.CancelledError:
                raise
            except (SpaPoolConnectionError, ConnectionError, OSError) as err:
                self._last_error = str(err)
                _LOGGER.warning(
                    "Spa stream error for %s:%s: %s",
                    self.host,
                    self.port,
                    err,
                )
            except Exception as err:  # noqa: BLE001
                self._last_error = str(err)
                _LOGGER.exception(
                    "Unexpected spa stream error for %s:%s",
                    self.host,
                    self.port,
                )
            finally:
                await self._async_close_connection()
                self._set_available(False)
                self._protocol.reset_stream()

            delay_before_retry = reconnect_delay

            if self._current_connection_received_frame:
                reconnect_delay = _RECONNECT_INITIAL_DELAY
            else:
                reconnect_delay = min(
                    reconnect_delay * 2,
                    _RECONNECT_MAX_DELAY,
                )

            if has_connected_before:
                self._reconnect_count += 1
            has_connected_before = True

            _LOGGER.debug(
                "Reconnecting to spa bridge in %.1f seconds",
                delay_before_retry,
            )
            await asyncio.sleep(delay_before_retry)

    async def _async_connect_and_listen(self) -> None:
        """Open one connection and consume it until it fails or becomes stale."""

        self._connection_attempts += 1
        _LOGGER.debug(
            "Connecting to spa bridge at %s:%s",
            self.host,
            self.port,
        )

        try:
            async with asyncio.timeout(_CONNECT_TIMEOUT):
                reader, writer = await asyncio.open_connection(
                    self.host,
                    self.port,
                )
        except (OSError, TimeoutError) as err:
            raise SpaPoolConnectionError(
                f"Unable to connect to {self.host}:{self.port}"
            ) from err

        self._reader = reader
        self._writer = writer
        self._connected = True

        loop = asyncio.get_running_loop()
        valid_frame_deadline = loop.time() + _STREAM_STALE_TIMEOUT

        while True:
            remaining = valid_frame_deadline - loop.time()
            if remaining <= 0:
                raise SpaPoolConnectionError(
                    "Spa stream became stale: no valid frame received"
                )

            try:
                async with asyncio.timeout(remaining):
                    data = await reader.read(_READ_SIZE)
            except TimeoutError as err:
                raise SpaPoolConnectionError(
                    "Spa stream became stale: no valid frame received"
                ) from err

            if not data:
                raise SpaPoolConnectionError("Spa bridge closed the TCP stream")

            try:
                updates = self._protocol.feed_data(data)
            except SpaPoolProtocolError as err:
                self._protocol_error_count += 1
                self._last_error = str(err)
                _LOGGER.warning("Discarding malformed spa data: %s", err)
                continue

            if not updates:
                continue

            valid_frame_deadline = loop.time() + _STREAM_STALE_TIMEOUT
            self._current_connection_received_frame = True

            for update in updates:
                await self._async_process_update(update)

    async def _async_process_update(self, update: Any) -> None:
        """Record one validated protocol update and notify subscribers."""

        previous_state = self._state

        self._valid_frame_count += 1
        self._last_frame = update.raw_frame
        self._last_message_type = update.message_type
        self._last_message_at = datetime.now(UTC)
        self._last_error = None

        first_available_frame = not self._available
        if first_available_frame:
            self._available = True
            self._available_event.set()

        self._first_frame_event.set()

        # Message-level diagnostics (for example, the timestamp of the last
        # valid frame) must update for every accepted frame, even when the
        # decoded spa state itself has not changed.
        self._notify_message_listeners()

        if update.message_type == "ready_to_send":
            self._ready_revision += 1
            self._ready_to_send_count += 1
        elif update.message_type == "nothing_to_send":
            self._nothing_to_send_count += 1
        elif update.message_type == "bridge_idle":
            self._bridge_idle_count += 1

        if update.message_type == "device_configuration":
            async with self._device_configuration_condition:
                self._device_configuration_frame = bytes(update.raw_frame)
                self._device_configuration_payload = bytes(update.payload)
                self._device_configuration_at = self._last_message_at
                self._device_configuration_revision += 1
                self._device_configuration_condition.notify_all()

        state_received = update.state is not None
        regular_status_received = (
            update.message_type == "status_update"
            and update.state is not None
        )

        if state_received:
            async with self._state_condition:
                self._state = update.state
                if regular_status_received:
                    self._state_revision += 1
                    self._valid_status_count += 1
                self._state_condition.notify_all()

        fault = update.fault
        if fault is not None:
            async with self._fault_condition:
                self._last_fault = fault
                self._fault_revision += 1
                self._fault_condition.notify_all()

            self._notify_fault_listeners(fault)

        if (
            first_available_frame
            or (state_received and self._state != previous_state)
        ):
            self._notify_listeners()

    async def _async_close_connection(self) -> None:
        """Close and forget the current stream objects."""

        writer = self._writer
        self._reader = None
        self._writer = None
        self._connected = False

        if writer is None:
            return

        writer.close()
        with suppress(ConnectionError, OSError, TimeoutError):
            async with asyncio.timeout(_CLOSE_TIMEOUT):
                await writer.wait_closed()

    def _set_available(self, available: bool) -> None:
        """Set protocol availability and notify entities when it changes."""

        if self._available == available:
            if not available:
                self._available_event.clear()
            return

        self._available = available
        if available:
            self._available_event.set()
        else:
            self._available_event.clear()

        self._notify_listeners()

    def _notify_listeners(self) -> None:
        """Call listeners without allowing one entity to break the stream."""

        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Spa Pool update listener failed")

    def _notify_message_listeners(self) -> None:
        """Call per-frame listeners without allowing one to break the stream."""

        for listener in tuple(self._message_listeners):
            try:
                listener()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Spa Pool message listener failed")

    def _notify_fault_listeners(self, fault: SpaFault) -> None:
        """Call fault listeners without allowing one to break the stream."""

        for listener in tuple(self._fault_listeners):
            try:
                listener(fault)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Spa Pool fault listener failed")

    def diagnostics(self) -> dict[str, Any]:
        """Return transport diagnostics without configuration credentials."""

        return {
            "connected": self._connected,
            "available": self._available,
            "state_revision": self._state_revision,
            "fault_revision": self._fault_revision,
            "device_configuration": {
                "received": self._device_configuration_frame is not None,
                "revision": self._device_configuration_revision,
                "received_at": (
                    self._device_configuration_at.isoformat()
                    if self._device_configuration_at is not None
                    else None
                ),
                "raw_frame": (
                    self._device_configuration_frame.hex()
                    if self._device_configuration_frame is not None
                    else None
                ),
                "payload": (
                    self._device_configuration_payload.hex()
                    if self._device_configuration_payload is not None
                    else None
                ),
                "payload_length": (
                    len(self._device_configuration_payload)
                    if self._device_configuration_payload is not None
                    else None
                ),
                "decoded": _decode_device_configuration(
                    self._device_configuration_payload
                ),
            },
            "ready_revision": self._ready_revision,
            "command_in_progress": self._command_lock.locked(),
            "last_message_at": (
                self._last_message_at.isoformat()
                if self._last_message_at is not None
                else None
            ),
            "last_message_type": self._last_message_type,
            "last_error": self._last_error,
            "connection_attempts": self._connection_attempts,
            "reconnect_count": self._reconnect_count,
            "valid_frame_count": self._valid_frame_count,
            "valid_status_count": self._valid_status_count,
            "ready_to_send_count": self._ready_to_send_count,
            "nothing_to_send_count": self._nothing_to_send_count,
            "bridge_idle_count": self._bridge_idle_count,
            "protocol_error_count": self._protocol_error_count,
            "protocol": self._protocol.diagnostics(),
        }



    async def __aenter__(self) -> SpaPoolClient:
        """Start the client when used as an async context manager."""

        await self.async_start()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        """Stop the client when leaving an async context manager."""

        await self.async_stop()


def _decode_device_configuration(payload: bytes | None) -> dict[str, Any] | None:
    """Decode only the controller fields verified for this installation.

    The first two payload bytes use two bits per pump. A value of 0 means the
    pump is absent, 1 means single-speed, and 2 means two-speed. Remaining
    payload bytes are retained raw because their exact equipment mapping has
    not yet been verified on this controller.
    """

    if payload is None or len(payload) < 2:
        return None

    pump_speed_codes = [
        (payload[0] >> shift) & 0x03 for shift in (0, 2, 4, 6)
    ]
    pump_speed_codes.extend(
        (payload[1] >> shift) & 0x03 for shift in (0, 2)
    )

    return {
        "pump_speed_codes": pump_speed_codes,
        "pump_count": sum(code != 0 for code in pump_speed_codes),
        "verified_pump_speeds": [
            code if code in (1, 2) else 0 for code in pump_speed_codes
        ],
        "unparsed_payload_tail": payload[2:].hex(),
    }
