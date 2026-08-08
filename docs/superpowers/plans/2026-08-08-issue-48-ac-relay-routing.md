# Issue 48 AC Relay Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore per-device AC1/AC2 control in multi-device STREAM/BKW stacks while keeping all system-wide commands routed through the resolved main device SN.

**Architecture:** The coordinator will derive a target SN from the command payload: `cfgRelay2Onoff` and `cfgRelay3Onoff` target the configured physical device, while every other command targets `command_sn` (the resolved main SN). The MQTT client will publish to the target SN carried by the prepared payload and subscribe to replies for both the configured and resolved-main devices.

**Tech Stack:** Python 3.12, Home Assistant custom integration, asyncio, paho-mqtt, pytest.

## Global Constraints

- Preserve state/quota/status reads on the configured device SN.
- Route only AC1/AC2 relay parameters to the configured device SN.
- Continue routing backup reserve, charge/discharge limits, operating mode, feed-in, and base-load commands to the resolved main SN.
- Keep single-device behavior unchanged when `device_sn == command_sn`.
- Do not require real device credentials in automated tests.

---

### Task 1: Route Commands by Scope

**Files:**
- Create: `tests/test_bkw_command_routing.py`
- Modify: `tests/test_stream_microinverter_mappings.py`
- Modify: `custom_components/ecoflow_api/coordinator.py`
- Modify: `custom_components/ecoflow_api/hybrid_coordinator.py`
- Modify: `custom_components/ecoflow_api/mqtt_client.py`

**Interfaces:**
- Consumes: command dictionaries containing `sn` and a `params` mapping; coordinator `device_sn` and `command_sn` values.
- Produces: `EcoFlowDataCoordinator._prepare_command(command) -> tuple[dict[str, Any], str]`, where the returned payload is copied and its `sn` matches the returned target SN.
- Produces: MQTT publication on `/open/{certificate_account}/{target_sn}/set` and subscriptions for `/set_reply` on both allowed command targets.

- [ ] **Step 1: Write failing coordinator routing tests**

```python
@pytest.mark.parametrize("param_key", ["cfgRelay2Onoff", "cfgRelay3Onoff"])
async def test_rest_routes_ac_relays_to_configured_device(param_key):
    coordinator = make_rest_coordinator(device_sn="DEVICE", command_sn="MAIN")
    await coordinator.async_send_command({"sn": "DEVICE", "params": {param_key: True}})
    assert coordinator.client.calls[0]["device_sn"] == "DEVICE"

async def test_rest_routes_system_commands_to_main_device():
    coordinator = make_rest_coordinator(device_sn="DEVICE", command_sn="MAIN")
    await coordinator.async_send_command({"sn": "DEVICE", "params": {"backupRatio": 20}})
    assert coordinator.client.calls[0]["device_sn"] == "MAIN"
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run: `C:\Users\Taras\ecoflow-api-mqtt\.venv\Scripts\python.exe -m pytest tests/test_bkw_command_routing.py -q`

Expected: AC relay cases fail because current coordinators overwrite every payload SN with `command_sn`; MQTT case fails because it publishes every command to the main-device topic.

- [ ] **Step 3: Implement coordinator target selection**

```python
BKW_DEVICE_SCOPED_PARAMS = frozenset({"cfgRelay2Onoff", "cfgRelay3Onoff"})

def _prepare_command(self, command: dict[str, Any]) -> tuple[dict[str, Any], str]:
    prepared = dict(command)
    params = prepared.get("params")
    target_sn = self.command_sn
    if isinstance(params, dict) and BKW_DEVICE_SCOPED_PARAMS.intersection(params):
        target_sn = self.device_sn
    prepared["sn"] = target_sn
    return prepared, target_sn
```

Use the prepared copy and target SN in both REST and hybrid coordinator send paths so caller-owned payloads are not mutated.

- [ ] **Step 4: Implement target-aware MQTT topics**

```python
allowed_target_sns = {self.device_sn, self.command_sn}
target_sn = mqtt_command.get("sn")
if target_sn not in allowed_target_sns:
    target_sn = self.command_sn
set_topic = f"/open/{self._certificate_account}/{target_sn}/set"
```

Subscribe to both distinct `/set_reply` topics during connect and accept replies from either topic in `_on_message`.

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `C:\Users\Taras\ecoflow-api-mqtt\.venv\Scripts\python.exe -m pytest tests/test_bkw_command_routing.py -q`

Expected: all command-routing tests pass.

- [ ] **Step 6: Run complete regression suite**

Run: `C:\Users\Taras\ecoflow-api-mqtt\.venv\Scripts\python.exe -m pytest -q`

Expected: all tests pass; the existing Home Assistant dependency warning may remain.

- [ ] **Step 7: Commit the isolated fix**

```powershell
git add -f tests/test_bkw_command_routing.py
git add custom_components/ecoflow_api/coordinator.py custom_components/ecoflow_api/hybrid_coordinator.py custom_components/ecoflow_api/mqtt_client.py docs/superpowers/plans/2026-08-08-issue-48-ac-relay-routing.md
git commit -m "fix: route BKW AC relays to physical devices"
```
