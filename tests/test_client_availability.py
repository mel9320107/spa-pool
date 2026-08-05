"""Tests for independent transport and status availability."""

from __future__ import annotations

import asyncio
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


# Import the Home Assistant-independent client without executing the
# integration package's Home Assistant entrypoint.
ROOT = Path(__file__).parents[1]
CUSTOM_COMPONENTS = ROOT / "custom_components"
SPA_POOL = CUSTOM_COMPONENTS / "spa_pool"

custom_components = ModuleType("custom_components")
custom_components.__path__ = [str(CUSTOM_COMPONENTS)]
spa_pool = ModuleType("custom_components.spa_pool")
spa_pool.__path__ = [str(SPA_POOL)]
sys.modules.setdefault("custom_components", custom_components)
sys.modules.setdefault("custom_components.spa_pool", spa_pool)

from custom_components.spa_pool import client as client_module  # noqa: E402
from custom_components.spa_pool.client import SpaPoolClient  # noqa: E402


def _update(message_type: str, *, state: object | None = None) -> object:
    """Build the subset of a decoded update used by the client."""

    return SimpleNamespace(
        message_type=message_type,
        raw_frame=b"\x7e\x00\x7e",
        payload=b"",
        state=state,
        fault=None,
    )


class SpaPoolAvailabilityTests(unittest.IsolatedAsyncioTestCase):
    """Exercise transport and state availability as separate signals."""

    async def asyncTearDown(self) -> None:
        """Allow any cancelled timer callbacks to be discarded."""

        await asyncio.sleep(0)

    async def test_non_status_frame_only_makes_transport_available(self) -> None:
        """Bus-management traffic must not validate retained spa state."""

        client = SpaPoolClient("spa.local", 8899)

        await client._async_process_update(_update("ready_to_send"))

        self.assertTrue(client.transport_available)
        self.assertFalse(client.state_available)
        self.assertFalse(client.available)
        self.assertIsNone(client.last_status_at)

    async def test_status_freshness_expires_without_dropping_transport(
        self,
    ) -> None:
        """Status state becomes unavailable while valid transport remains."""

        client = SpaPoolClient("spa.local", 8899)
        retained_state = object()
        original_timeout = client_module._STATUS_STALE_TIMEOUT
        client_module._STATUS_STALE_TIMEOUT = 0.01
        self.addCleanup(
            setattr,
            client_module,
            "_STATUS_STALE_TIMEOUT",
            original_timeout,
        )

        await client._async_process_update(
            _update("status_update", state=retained_state)
        )

        self.assertTrue(client.transport_available)
        self.assertTrue(client.state_available)
        self.assertIs(client.state, retained_state)
        self.assertIsNotNone(client.last_status_at)

        await asyncio.sleep(0.02)

        self.assertTrue(client.transport_available)
        self.assertFalse(client.state_available)
        self.assertIs(client.state, retained_state)

    async def test_disconnect_clears_both_availability_signals(self) -> None:
        """A disconnect invalidates transport and status immediately."""

        client = SpaPoolClient("spa.local", 8899)
        await client._async_process_update(
            _update("status_update", state=object())
        )

        client._set_transport_available(False)
        client._set_state_available(False)

        self.assertFalse(client.transport_available)
        self.assertFalse(client.state_available)
        self.assertFalse(client._transport_available_event.is_set())
        self.assertFalse(client._state_available_event.is_set())


if __name__ == "__main__":
    unittest.main()
