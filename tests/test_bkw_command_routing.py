"""Behavioral regression coverage for BKW per-device AC relay routing."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.ecoflow_api.const import (
    DEVICE_TYPE_DELTA_PRO_3,
    DEVICE_TYPE_STREAM_MICRO_INVERTER,
    DEVICE_TYPE_STREAM_ULTRA,
    DEVICE_TYPE_STREAM_ULTRA_X,
)
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


class FailingMqttCommandClient:
    """Record hybrid MQTT dispatch, then force the REST fallback path."""

    def __init__(self) -> None:
        self.commands: list[dict[str, Any]] = []

    async def async_publish_command(
        self, command: dict[str, Any], ack_timeout: float | None = None
    ) -> bool:
        self.commands.append(command)
        return False


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
    device_sn: str, command_sn: str, device_type: str = DEVICE_TYPE_DELTA_PRO_3
) -> EcoFlowHybridCoordinator:
    """Build the real hybrid command path with MQTT deliberately unavailable."""
    coordinator = object.__new__(EcoFlowHybridCoordinator)
    coordinator.device_sn = device_sn
    coordinator.command_sn = command_sn
    coordinator.device_type = device_type
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


async def test_hybrid_rejects_mixed_scope_before_rest_or_mqtt_dispatch() -> None:
    """A relay combined with backupRatio cannot reach either command boundary."""
    coordinator = make_hybrid_coordinator(device_sn="DEVICE", command_sn="MAIN")
    mqtt_client = FailingMqttCommandClient()
    coordinator._mqtt_connected = True
    coordinator._mqtt_client = mqtt_client
    command = {
        "sn": "CALLER",
        "params": {"cfgRelay2Onoff": True, "backupRatio": 20},
    }

    with pytest.raises(ValueError, match="mixed device- and system-scoped"):
        await coordinator.async_send_command(command)

    assert mqtt_client.commands == []
    assert coordinator.client.calls == []
    assert command == {
        "sn": "CALLER",
        "params": {"cfgRelay2Onoff": True, "backupRatio": 20},
    }


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


async def test_mqtt_ack_resolves_only_its_matching_target_command() -> None:
    """Concurrent DEVICE and MAIN commands sharing an ID require matching ACKs."""
    loop = asyncio.get_running_loop()
    transport = RecordingMqttTransport()
    client = EcoFlowMQTTClient(
        username="user",
        password="password",
        device_sn="DEVICE",
        command_sn="MAIN",
        certificate_account="ACCOUNT",
        loop=loop,
    )
    client._connected = True
    client._client = transport

    device_command = asyncio.create_task(
        client.async_publish_command(
            {"id": 42, "sn": "DEVICE", "params": {"cfgRelay2Onoff": True}},
            ack_timeout=1,
        )
    )
    main_command = asyncio.create_task(
        client.async_publish_command(
            {"id": 42, "sn": "MAIN", "params": {"backupRatio": 20}},
            ack_timeout=1,
        )
    )

    try:
        for _ in range(10):
            if len(transport.published) == 2:
                break
            await asyncio.sleep(0)
        assert len(transport.published) == 2

        client._on_message(
            None,
            None,
            SimpleNamespace(
                topic="/open/ACCOUNT/DEVICE/set_reply",
                payload=b'{"id": 42, "data": {"result": 0}}',
            ),
        )

        assert await asyncio.wait_for(asyncio.shield(device_command), 0.1) is True
        assert not main_command.done()

        client._on_message(
            None,
            None,
            SimpleNamespace(
                topic="/open/ACCOUNT/MAIN/set_reply",
                payload=b'{"id": 42, "data": {"result": 0}}',
            ),
        )
        assert await asyncio.wait_for(main_command, 0.1) is True
    finally:
        for command in (device_command, main_command):
            if not command.done():
                command.cancel()
        await asyncio.gather(device_command, main_command, return_exceptions=True)


async def test_hybrid_fallback_to_rest_does_not_emit_warnings_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MQTT timeout/publish failure during normal REST fallback must not spam warnings/errors."""
    coordinator = make_hybrid_coordinator(device_sn="DEVICE", command_sn="MAIN")
    mqtt_client = FailingMqttCommandClient()
    coordinator._mqtt_connected = True
    coordinator._mqtt_client = mqtt_client
    command = {"sn": "CALLER", "params": {"cfgRelay2Onoff": True}}

    with caplog.at_level("WARNING"):
        await coordinator.async_send_command(command)

    assert coordinator.client.calls == [
        {
            "device_sn": "DEVICE",
            "cmd_code": {"sn": "DEVICE", "params": {"cfgRelay2Onoff": True}},
        }
    ]
    warning_or_error_records = [
        record for record in caplog.records if record.levelname in ("WARNING", "ERROR")
    ]
    assert warning_or_error_records == []


async def test_mqtt_command_timeout_does_not_emit_warnings_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MQTT command timeout while awaiting set_reply must log at DEBUG, not WARNING/ERROR."""
    loop = asyncio.get_running_loop()
    transport = RecordingMqttTransport()
    client = EcoFlowMQTTClient(
        username="user",
        password="password",
        device_sn="DEVICE",
        command_sn="MAIN",
        certificate_account="ACCOUNT",
        loop=loop,
    )
    client._connected = True
    client._client = transport

    with caplog.at_level("WARNING"):
        success = await client.async_publish_command(
            {"sn": "DEVICE", "params": {"cfgRelay2Onoff": True}},
            ack_timeout=0.01,
        )

    assert success is False
    warning_or_error_records = [
        record for record in caplog.records if record.levelname in ("WARNING", "ERROR")
    ]
    assert warning_or_error_records == []


@pytest.mark.parametrize(
    "stream_type",
    [
        DEVICE_TYPE_STREAM_ULTRA_X,
        DEVICE_TYPE_STREAM_ULTRA,
        DEVICE_TYPE_STREAM_MICRO_INVERTER,
    ],
)
async def test_stream_hybrid_routes_commands_directly_to_rest_without_mqtt_delay(
    stream_type: str,
) -> None:
    """STREAM devices bypass MQTT command publish to eliminate the 5s timeout penalty."""
    coordinator = make_hybrid_coordinator(
        device_sn="DEVICE", command_sn="MAIN", device_type=stream_type
    )
    mqtt_client = FailingMqttCommandClient()
    coordinator._mqtt_connected = True
    coordinator._mqtt_client = mqtt_client
    command = {"sn": "CALLER", "params": {"cfgRelay2Onoff": True}}

    await coordinator.async_send_command(command)

    # MQTT command publish was never attempted (0 delay)
    assert mqtt_client.commands == []
    # REST API was invoked directly
    assert coordinator.client.calls == [
        {
            "device_sn": "DEVICE",
            "cmd_code": {"sn": "DEVICE", "params": {"cfgRelay2Onoff": True}},
        }
    ]


async def test_non_stream_hybrid_tries_mqtt_command_first() -> None:
    """Non-stream devices (e.g. Delta Pro 3) continue to try MQTT first."""
    coordinator = make_hybrid_coordinator(
        device_sn="DEVICE", command_sn="MAIN", device_type=DEVICE_TYPE_DELTA_PRO_3
    )
    mqtt_client = FailingMqttCommandClient()
    coordinator._mqtt_connected = True
    coordinator._mqtt_client = mqtt_client
    command = {"sn": "DEVICE", "params": {"acOut": True}}

    await coordinator.async_send_command(command)

    # MQTT command publish was attempted
    assert len(mqtt_client.commands) == 1
    # Then fell back to REST because FailingMqttCommandClient returned False
    assert len(coordinator.client.calls) == 1



