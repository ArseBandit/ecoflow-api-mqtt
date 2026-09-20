"""Regression coverage for Stream Custom operating-mode display (issue #68).

Drives the actual EcoFlowStreamSelect class with a fake coordinator (no I/O).
Custom is display-only: selecting it must raise without any device request.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from custom_components.ecoflow_api.select import (
    EcoFlowStreamSelect,
    STREAM_ULTRA_X_SELECT_DEFINITIONS,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "issue68_custom_mode.json").read_text()
)
DEF = STREAM_ULTRA_X_SELECT_DEFINITIONS["operating_mode"]


def make_entity(
    data: dict[str, Any] | None, device_type: str = "stream_ultra_x"
) -> tuple[EcoFlowStreamSelect, SimpleNamespace]:
    """Build the real entity with a fake coordinator (no I/O)."""
    coordinator = SimpleNamespace(
        device_sn="SN",
        command_sn="SN",
        device_type=device_type,
        data=data,
        calls=[],
        refreshes=0,
    )

    async def fake_send(command: dict[str, Any]) -> bool:
        coordinator.calls.append(command)
        return True

    async def fake_refresh() -> None:
        coordinator.refreshes += 1

    coordinator.async_send_command = fake_send
    coordinator.async_request_refresh = fake_refresh
    entry = SimpleNamespace(entry_id="test_entry")
    return EcoFlowStreamSelect(coordinator, entry, "operating_mode", DEF), coordinator


class StreamCustomDisplayTest(unittest.TestCase):
    def test_ultra_x_offers_custom_option(self) -> None:
        entity, _ = make_entity({})
        self.assertIn("Custom", entity.options)
        self.assertIn("Self-Powered", entity.options)
        self.assertIn("AI Mode", entity.options)

    def test_other_stream_model_has_no_custom_option(self) -> None:
        for device_type in ("stream_ac_pro", "stream_ultra"):
            with self.subTest(device_type=device_type):
                entity, _ = make_entity({}, device_type=device_type)
                self.assertNotIn("Custom", entity.options)

    def test_captured_ai_payload_reports_ai_mode(self) -> None:
        entity, _ = make_entity(dict(FIXTURE["ai_mode"]))
        self.assertEqual(entity.current_option, "AI Mode")

    def test_captured_custom_payload_reports_custom(self) -> None:
        entity, _ = make_entity(dict(FIXTURE["custom_mode"]))
        self.assertEqual(entity.current_option, "Custom")

    def test_custom_nested_form_reports_custom(self) -> None:
        entity, _ = make_entity(
            {
                "energyStrategyOperateMode": {
                    "operateSelfPoweredOpen": False,
                    "operateIntelligentScheduleModeOpen": False,
                }
            }
        )
        self.assertEqual(entity.current_option, "Custom")

    def test_self_powered_preserved(self) -> None:
        entity, _ = make_entity(
            {
                "energyStrategyOperateMode.operateSelfPoweredOpen": True,
                "energyStrategyOperateMode.operateIntelligentScheduleModeOpen": False,
            }
        )
        self.assertEqual(entity.current_option, "Self-Powered")

    def test_partial_empty_malformed_never_custom(self) -> None:
        for data in (
            {},
            {"energyStrategyOperateMode.operateSelfPoweredOpen": False},
            {"energyStrategyOperateMode.operateIntelligentScheduleModeOpen": False},
            {
                "energyStrategyOperateMode.operateSelfPoweredOpen": None,
                "energyStrategyOperateMode.operateIntelligentScheduleModeOpen": None,
            },
            {
                "energyStrategyOperateMode.operateSelfPoweredOpen": 0,
                "energyStrategyOperateMode.operateIntelligentScheduleModeOpen": 0,
            },
            {
                "energyStrategyOperateMode.operateSelfPoweredOpen": "false",
                "energyStrategyOperateMode.operateIntelligentScheduleModeOpen": "false",
            },
        ):
            with self.subTest(data=data):
                entity, _ = make_entity(dict(data))
                self.assertIsNone(entity.current_option)

    def test_other_active_mode_is_not_custom(self) -> None:
        for flag in ("operateTouModeOpen", "operateScheduledOpen", "operateFutureModeOpen"):
            with self.subTest(flag=flag):
                entity, _ = make_entity(
                    {
                        "energyStrategyOperateMode.operateSelfPoweredOpen": False,
                        "energyStrategyOperateMode.operateIntelligentScheduleModeOpen": (
                            False
                        ),
                        f"energyStrategyOperateMode.{flag}": True,
                    }
                )
                self.assertIsNone(entity.current_option)

    def test_nested_unknown_active_flag_is_not_custom(self) -> None:
        entity, _ = make_entity(
            {
                "energyStrategyOperateMode": {
                    "operateSelfPoweredOpen": False,
                    "operateIntelligentScheduleModeOpen": False,
                    "operateFutureModeOpen": True,
                }
            }
        )
        self.assertIsNone(entity.current_option)

    def test_other_model_uses_truthiness_not_strict_true(self) -> None:
        # Plain/other Stream models keep old truthiness: a truthy non-bool still
        # reports the mode, and the strict both-False Custom path never applies.
        entity, _ = make_entity(
            {"energyStrategyOperateMode.operateSelfPoweredOpen": 1},
            device_type="stream_ultra",
        )
        self.assertEqual(entity.current_option, "Self-Powered")
        entity2, _ = make_entity(dict(FIXTURE["custom_mode"]), device_type="stream_ultra")
        self.assertIsNone(entity2.current_option)

    def test_custom_both_false_on_other_model_stays_unknown(self) -> None:
        entity, _ = make_entity(
            dict(FIXTURE["custom_mode"]), device_type="stream_ac_pro"
        )
        self.assertIsNone(entity.current_option)


class StreamCustomSelectRejectTest(unittest.IsolatedAsyncioTestCase):
    async def test_selecting_custom_raises_without_device_request(self) -> None:
        entity, coordinator = make_entity(dict(FIXTURE["custom_mode"]))
        with self.assertRaises(HomeAssistantError):
            await entity.async_select_option("Custom")
        self.assertEqual(coordinator.calls, [])
        self.assertEqual(coordinator.refreshes, 0)

    async def test_self_powered_payload_unchanged(self) -> None:
        entity, coordinator = make_entity(dict(FIXTURE["ai_mode"]))
        await entity.async_select_option("Self-Powered")
        self.assertEqual(len(coordinator.calls), 1)
        self.assertEqual(
            coordinator.calls[0]["params"],
            {"cfgEnergyStrategyOperateMode": {"operateSelfPoweredOpen": True}},
        )

    async def test_ai_mode_payload_unchanged(self) -> None:
        entity, coordinator = make_entity(dict(FIXTURE["custom_mode"]))
        await entity.async_select_option("AI Mode")
        self.assertEqual(len(coordinator.calls), 1)
        self.assertEqual(
            coordinator.calls[0]["params"],
            {
                "cfgEnergyStrategyOperateMode": {
                    "operateIntelligentScheduleModeOpen": True
                }
            },
        )

    async def test_invalid_option_sends_nothing(self) -> None:
        entity, coordinator = make_entity(dict(FIXTURE["ai_mode"]))
        await entity.async_select_option("Turbo")
        self.assertEqual(coordinator.calls, [])


if __name__ == "__main__":
    unittest.main()
