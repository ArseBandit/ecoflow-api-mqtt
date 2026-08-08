"""Behavioral regression coverage for BKW per-device AC relay routing."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.ecoflow_api.coordinator import EcoFlowDataCoordinator
from custom_components.ecoflow_api.hybrid_coordinator import EcoFlowHybridCoordinator
from custom_components.ecoflow_api.mqtt_client import EcoFlowMQTTClient, mqtt


class RecordingApiClient:
    """Record the API boundary invoked by a coordinator command."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def set_device_quota(
        self, device_sn: str, cmd_code: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append({"device_sn": device_sn, "cmd_code": cmd_code})
        return {"ok": True}


class RecordingMqttTransport:
    """Minimal stand-in for the paho network boundary."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, int]] = []
        self.subscriptions: list[tuple[str, int]] = []

    def publish(self, topic: str, payload: str, qos: int) -> SimpleNamespace:
        self.published.append((topic, payload, qos))
        return SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)

    def subscribe(self, topic: str, qos: int) -> None:
        self.subscriptions.append((topic, qos))


def make_rest_coordinator(
    device_sn: str, command_sn: str
) -> EcoFlowDataCoordinator:
    """Build the real command method without Home Assistant setup/network I/O."""
    coordinator = object.__new__(EcoFlowDataCoordinator)
    coordinator.device_sn = device_sn
    coordinator.command_sn = command_sn
    coordinator.client = RecordingApiClient()
    return coordinator


def make_hybrid_coordinator(
    device_sn: str, command_sn: str
) -> EcoFlowHybridCoordinator:
    """Build the real hybrid command path with MQTT deliberately unavailable."""
    coordinator = object.__new__(EcoFlowHybridCoordinator)
    coordinator.device_sn = device_sn
    coordinator.command_sn = command_sn
    coordinator.client = RecordingApiClient()
    coordinator._mqtt_connected = False
    coordinator._mqtt_client = None
    return coordinator


@pytest.mark.parametrize("param_key", ["cfgRelay2Onoff", "cfgRelay3Onoff"])
async def test_rest_routes_ac_relays_to_configured_device(param_key: str) -> None:
    """A relay command must address its physical configured device, not MAIN."""
    coordinator = make_rest_coordinator(device_sn="DEVICE", command_sn="MAIN")
    command = {"sn": "CALLER", "params": {param_key: True}}

    await coordinator.async_send_command(command)

    assert coordinator.client.calls == [
        {
            "device_sn": "DEVICE",
            "cmd_code": {"sn": "DEVICE", "params": {param_key: True}},
        }
    ]
    assert command == {"sn": "CALLER", "params": {param_key: True}}


async def test_rest_routes_system_commands_to_main_device() -> None:
    """A system-wide command must continue addressing the resolved main device."""
    coordinator = make_rest_coordinator(device_sn="DEVICE", command_sn="MAIN")
    command = {"sn": "CALLER", "params": {"backupRatio": 20}}

    await coordinator.async_send_command(command)

    assert coordinator.client.calls == [
        {
            "device_sn": "MAIN",
            "cmd_code": {"sn": "MAIN", "params": {"backupRatio": 20}},
        }
    ]
    assert command == {"sn": "CALLER", "params": {"backupRatio": 20}}


async def test_hybrid_fallback_preserves_device_scoped_relay_target() -> None:
    """When MQTT is unavailable, hybrid REST fallback keeps the relay target."""
    coordinator = make_hybrid_coordinator(device_sn="DEVICE", command_sn="MAIN")
    command = {"sn": "CALLER", "params": {"cfgRelay2Onoff": True}}

    await coordinator.async_send_command(command)

    assert coordinator.client.calls == [
        {
            "device_sn": "DEVICE",
            "cmd_code": {"sn": "DEVICE", "params": {"cfgRelay2Onoff": True}},
        }
    ]
    assert command == {"sn": "CALLER", "params": {"cfgRelay2Onoff": True}}


async def test_mqtt_publishes_command_to_target_sn() -> None:
    """A prepared relay payload must be published on the physical device topic."""
    transport = RecordingMqttTransport()
    client = EcoFlowMQTTClient(
        username="user",
        password="password",
        device_sn="DEVICE",
        command_sn="MAIN",
        certificate_account="ACCOUNT",
    )
    client._connected = True
    client._client = transport

    published = await client.async_publish_command(
        {"sn": "DEVICE", "params": {"cfgRelay2Onoff": True}}
    )

    assert published is True
    assert transport.published[0][0] == "/open/ACCOUNT/DEVICE/set"
    assert json.loads(transport.published[0][1])["sn"] == "DEVICE"


def test_mqtt_subscribes_to_both_distinct_command_reply_topics() -> None:
    """Replies for either a physical device or MAIN must be received."""
    transport = RecordingMqttTransport()
    client = EcoFlowMQTTClient(
        username="user",
        password="password",
        device_sn="DEVICE",
        command_sn="MAIN",
        certificate_account="ACCOUNT",
    )

    client._on_connect(transport, None, {"session present": False}, 0)

    assert {
        "/open/ACCOUNT/DEVICE/set_reply",
        "/open/ACCOUNT/MAIN/set_reply",
    }.issubset({topic for topic, _ in transport.subscriptions})


@pytest.mark.parametrize("target_sn", ["DEVICE", "MAIN"])
async def test_mqtt_accepts_ack_from_each_allowed_reply_topic(target_sn: str) -> None:
    """An ACK from either permitted command target resolves its pending command."""
    loop = asyncio.get_running_loop()
    client = EcoFlowMQTTClient(
        username="user",
        password="password",
        device_sn="DEVICE",
        command_sn="MAIN",
        certificate_account="ACCOUNT",
        loop=loop,
    )
    reply = loop.create_future()
    client._pending_acks[42] = reply

    client._on_message(
        None,
        None,
        SimpleNamespace(
            topic=f"/open/ACCOUNT/{target_sn}/set_reply",
            payload=b'{"id": 42, "data": {"result": 0}}',
        ),
    )

    assert await asyncio.wait_for(reply, timeout=0.1) == {"result": 0}
