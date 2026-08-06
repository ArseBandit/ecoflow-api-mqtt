"""Regression coverage for Stream Microinverter device mappings."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from custom_components.ecoflow_api.sensor import EcoFlowStreamMicroSolarPowerSensor


ROOT = Path(__file__).resolve().parents[1]
CUSTOM_COMPONENT = ROOT / "custom_components" / "ecoflow_api"
DEVICE_TYPE = "DEVICE_TYPE_STREAM_MICRO_INVERTER"


def _source(path: str) -> str:
    return (CUSTOM_COMPONENT / path).read_text(encoding="utf-8")


class StreamMicroinverterMappingTest(unittest.TestCase):
    def _sensor_with_data(self, data: dict) -> EcoFlowStreamMicroSolarPowerSensor:
        sensor = object.__new__(EcoFlowStreamMicroSolarPowerSensor)
        sensor.coordinator = SimpleNamespace(data=data)
        return sensor

    def test_stream_microinverter_is_available_in_device_types(self) -> None:
        source = _source("const.py")
        tree = ast.parse(source)
        assignments = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                assignments.update(
                    {
                        target.id: node.value
                        for target in node.targets
                        if isinstance(target, ast.Name)
                    }
                )
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assignments[node.target.id] = node.value

        self.assertIn(DEVICE_TYPE, assignments)

        device_types = assignments["DEVICE_TYPES"]
        self.assertIsInstance(device_types, ast.Dict)
        keys = {ast.unparse(key) for key in device_types.keys}
        values = {ast.literal_eval(value) for value in device_types.values}

        self.assertIn(DEVICE_TYPE, keys)
        self.assertIn("Stream Microinverter", values)

    def test_stream_microinverter_platform_maps_are_registered(self) -> None:
        expected_maps = {
            "sensor.py": "DEVICE_SENSOR_MAP",
            "binary_sensor.py": "DEVICE_BINARY_SENSOR_MAP",
            "number.py": "DEVICE_NUMBER_MAP",
            "switch.py": "DEVICE_SWITCH_MAP",
            "select.py": "DEVICE_SELECT_MAP",
            "button.py": "DEVICE_BUTTON_MAP",
        }

        for filename, map_name in expected_maps.items():
            with self.subTest(filename=filename):
                source = _source(filename)
                self.assertIn(DEVICE_TYPE, source)
                self.assertIn(map_name, source)

    def test_stream_microinverter_adds_derived_solar_energy_sensor(self) -> None:
        source = _source("sensor.py")
        self.assertIn("class EcoFlowStreamMicroSolarPowerSensor", source)
        self.assertIn("powGetPv", source)
        self.assertIn("powGetPv2", source)
        self.assertIn(
            "EcoFlowStreamMicroSolarPowerSensor(coordinator=coordinator, entry=entry)",
            source,
        )
        self.assertIn("isinstance(sensor, EcoFlowStreamMicroSolarPowerSensor)", source)
        self.assertIn(
            "EcoFlowIntegralEnergySensor(hass, sensor, enabled_default=True)",
            source,
        )

    def test_stream_microinverter_solar_power_sums_numeric_values(self) -> None:
        sensor = self._sensor_with_data({"powGetPv": 10, "powGetPv2": 20})

        self.assertEqual(sensor.native_value, 30.0)

    def test_stream_microinverter_solar_power_ignores_invalid_values(self) -> None:
        sensor = self._sensor_with_data({"powGetPv": "not-a-number", "powGetPv2": 20})

        self.assertEqual(sensor.native_value, 20.0)
        self.assertIsNone(self._sensor_with_data({}).native_value)

    def test_diagnostics_redacts_mqtt_credentials(self) -> None:
        source = _source("diagnostics.py")
        self.assertIn("CONF_MQTT_USERNAME", source)
        self.assertIn("CONF_MQTT_PASSWORD", source)
        self.assertIn('"certificateAccount"', source)
        self.assertIn('"certificatePassword"', source)
        self.assertIn(
            "redacted_options = async_redact_data(entry.options, TO_REDACT)",
            source,
        )
        self.assertIn('"options": redacted_options', source)


if __name__ == "__main__":
    unittest.main()
