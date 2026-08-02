"""Shared constants for the Spa Pool integration."""

from __future__ import annotations

from typing import Final

# Integration identity
DOMAIN: Final = "spa_pool"

# Transparent Balboa-compatible TCP bridge
DEFAULT_PORT: Final = 4257

# Config-entry option keys
CONF_PUMP_COUNT: Final = "pump_count"
CONF_PUMP_SPEEDS: Final = "pump_speeds"
CONF_BLOWER_COUNT: Final = "blower_count"
CONF_BLOWER_SPEEDS: Final = "blower_speeds"
CONF_LIGHT_COUNT: Final = "light_count"

# Default capabilities for the current spa installation
DEFAULT_PUMP_COUNT: Final = 3
DEFAULT_PUMP_SPEEDS: Final = 2
DEFAULT_BLOWER_COUNT: Final = 1
DEFAULT_BLOWER_SPEEDS: Final = 1
DEFAULT_LIGHT_COUNT: Final = 2

# Verified independently controllable protocol limits. The status payload
# contains a second blower field, but only one blower command is documented.
MAX_PUMPS: Final = 6
MAX_BLOWERS: Final = 1
MAX_LIGHTS: Final = 4
