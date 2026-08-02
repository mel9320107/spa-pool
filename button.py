"""Button platform for spa panel commands and maintenance requests."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Final, override

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import SpaPoolConfigEntry
from .client import (
    SpaPoolCommandError,
    SpaPoolConnectionError,
    SpaPoolNotConnectedError,
)
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
from .models import SpaState
from .protocol import (
    SettingsCode,
    ToggleItem,
    build_fault_log_request,
    build_request_command,
    build_set_time_command,
    build_toggle_command,
)


class SpaPoolButtonAction(Enum):
    """Action performed by a stateless Spa Pool button."""

    RESTART_STREAM = auto()
    SYNC_CLOCK = auto()
    REFRESH_FAULT_LOG = auto()
    REFRESH_DEVICE_CONFIGURATION = auto()
    CLEAR_REMINDER = auto()


@dataclass(frozen=True, kw_only=True)
class SpaPoolButtonEntityDescription(ButtonEntityDescription):
    """Describe one Spa Pool maintenance button."""

    action: SpaPoolButtonAction
    requires_stream: bool = True


BUTTON_DESCRIPTIONS: Final[
    tuple[SpaPoolButtonEntityDescription, ...]
] = (
    SpaPoolButtonEntityDescription(
        key="restart_stream",
        translation_key="restart_stream",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.DIAGNOSTIC,
        action=SpaPoolButtonAction.RESTART_STREAM,
        requires_stream=False,
    ),
    SpaPoolButtonEntityDescription(
        key="sync_clock",
        translation_key="sync_clock",
        icon="mdi:clock-sync",
        entity_category=EntityCategory.CONFIG,
        action=SpaPoolButtonAction.SYNC_CLOCK,
    ),
    SpaPoolButtonEntityDescription(
        key="refresh_fault_log",
        translation_key="refresh_fault_log",
        icon="mdi:alert-circle-check-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        action=SpaPoolButtonAction.REFRESH_FAULT_LOG,
    ),
    SpaPoolButtonEntityDescription(
        key="refresh_device_configuration",
        name="Refresh device configuration",
        icon="mdi:cog-refresh-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        action=SpaPoolButtonAction.REFRESH_DEVICE_CONFIGURATION,
    ),
    SpaPoolButtonEntityDescription(
        key="clear_reminder",
        translation_key="clear_reminder",
        icon="mdi:wrench-clock-outline",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        action=SpaPoolButtonAction.CLEAR_REMINDER,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpaPoolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up stateless command buttons."""

    entities: list[ButtonEntity] = [
        SpaPoolButtonEntity(entry, description)
        for description in BUTTON_DESCRIPTIONS
    ]

    pump_count = _bounded_int(
        entry.options.get(CONF_PUMP_COUNT),
        default=DEFAULT_PUMP_COUNT,
        minimum=0,
        maximum=MAX_PUMPS,
    )
    entities.extend(
        SpaPoolToggleButtonEntity(
            entry=entry,
            name=f"Pump {index + 1} next state",
            unique_key=f"pump_{index + 1}_next_state",
            toggle_item=ToggleItem(ToggleItem.PUMP_1 + index),
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
        SpaPoolToggleButtonEntity(
            entry=entry,
            name=f"Blower {index + 1} next state",
            unique_key=f"blower_{index + 1}_next_state",
            toggle_item=ToggleItem.BLOWER,
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
        SpaPoolToggleButtonEntity(
            entry=entry,
            name=f"Light {index + 1} next mode",
            # Preserve the unique ID used by the previous light-mode button.
            unique_key=f"light_{index + 1}_next_mode",
            toggle_item=ToggleItem(ToggleItem.LIGHT_1 + index),
            icon="mdi:palette-outline",
        )
        for index in range(light_count)
    )

    entities.extend(
        (
            SpaPoolToggleButtonEntity(
                entry=entry,
                name="Heat mode next",
                unique_key="heat_mode_next",
                toggle_item=ToggleItem.HEAT_MODE,
                icon="mdi:radiator",
            ),
            SpaPoolToggleButtonEntity(
                entry=entry,
                name="Temperature range toggle",
                unique_key="temperature_range_toggle",
                toggle_item=ToggleItem.TEMPERATURE_RANGE,
                icon="mdi:thermometer-chevron-up",
                entity_category=EntityCategory.CONFIG,
            ),
        )
    )

    async_add_entities(entities)


class SpaPoolButtonEntity(ButtonEntity):
    """Represent one stateless maintenance or request action."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    entity_description: SpaPoolButtonEntityDescription

    def __init__(
        self,
        entry: SpaPoolConfigEntry,
        description: SpaPoolButtonEntityDescription,
    ) -> None:
        """Initialise one maintenance button."""

        self.entity_description = description
        self._entry = entry
        self._client = entry.runtime_data.client

        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    @override
    def available(self) -> bool:
        """Keep stream recovery available even while disconnected."""

        if not self.entity_description.requires_stream:
            return True

        return self._client.available

    @override
    async def async_press(self) -> None:
        """Perform the configured action without state-confirmation waits."""

        action = self.entity_description.action

        if action is SpaPoolButtonAction.RESTART_STREAM:
            await self._async_restart_stream()
            return

        try:
            if action is SpaPoolButtonAction.SYNC_CLOCK:
                await self._async_sync_clock()
                return

            if action is SpaPoolButtonAction.REFRESH_FAULT_LOG:
                await self._client.async_send_frame(build_fault_log_request())
                return

            if action is SpaPoolButtonAction.REFRESH_DEVICE_CONFIGURATION:
                await self._client.async_send_frame(
                    build_request_command(
                        SettingsCode.DEVICE_CONFIGURATION,
                        0x00,
                        0x01,
                    )
                )
                return

            if action is SpaPoolButtonAction.CLEAR_REMINDER:
                state = self._require_state()
                if state.reminder.active:
                    await self._client.async_send_frame(
                        build_toggle_command(ToggleItem.CLEAR_NOTIFICATION)
                    )
                return

        except (
            SpaPoolCommandError,
            SpaPoolConnectionError,
            SpaPoolNotConnectedError,
            ValueError,
        ) as err:
            raise HomeAssistantError(
                f"Unable to send {self.name.lower()} command: {err}"
            ) from err

        raise HomeAssistantError(
            f"Unsupported Spa Pool button action: {action}"
        )

    async def _async_restart_stream(self) -> None:
        """Restart the transport and await a valid frame."""

        try:
            await self._client.async_reconnect()
        except (SpaPoolConnectionError, OSError, TimeoutError) as err:
            raise HomeAssistantError(
                "Unable to restart the spa status stream"
            ) from err

    async def _async_sync_clock(self) -> None:
        """Send the current Home Assistant local time to the spa."""

        state = self._require_state()
        now = dt_util.now()
        await self._client.async_send_frame(
            build_set_time_command(
                now.hour,
                now.minute,
                clock_24_hour=state.clock_24_hour,
            )
        )

    def _require_state(self) -> SpaState:
        """Return current state or raise a user-visible communication error."""

        state = self._client.state
        if state is None or not self._client.available:
            raise HomeAssistantError("Spa status stream is unavailable")

        return state


class SpaPoolToggleButtonEntity(ButtonEntity):
    """Send exactly one Balboa toggle-state command."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        *,
        entry: SpaPoolConfigEntry,
        name: str,
        unique_key: str,
        toggle_item: ToggleItem,
        icon: str,
        entity_category: EntityCategory | None = None,
    ) -> None:
        """Initialise one virtual panel-button press."""

        self._entry = entry
        self._client = entry.runtime_data.client
        self._toggle_item = toggle_item

        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{unique_key}"
        self._attr_icon = icon
        self._attr_entity_category = entity_category
        self._attr_device_info = _device_info(entry)

    @property
    @override
    def available(self) -> bool:
        """Return whether the bridge currently accepts commands."""

        return self._client.available

    @override
    async def async_press(self) -> None:
        """Write one complete toggle frame and return."""

        try:
            await self._client.async_send_frame(
                build_toggle_command(self._toggle_item)
            )
        except (
            SpaPoolCommandError,
            SpaPoolConnectionError,
            SpaPoolNotConnectedError,
            ValueError,
        ) as err:
            raise HomeAssistantError(
                f"Unable to send {self.name.lower()} command: {err}"
            ) from err


def _device_info(entry: SpaPoolConfigEntry) -> DeviceInfo:
    """Return shared device-registry metadata."""

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Balboa-compatible",
        model="RS-485 spa controller",
    )


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
