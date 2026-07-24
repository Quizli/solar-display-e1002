import math
from typing import Any, Dict, Mapping, Optional, Tuple

from .model import LiveData


class FroniusDataError(ValueError):
    """Raised when required Fronius measurements cannot be normalized."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FroniusDataError(f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise FroniusDataError(f"{field} must be finite")
    return result


def _body(payload: Mapping[str, Any], source: str) -> Mapping[str, Any]:
    body = payload.get("Body")
    if not isinstance(body, Mapping):
        raise FroniusDataError(f"{source}.Body is missing or invalid")
    data = body.get("Data")
    if not isinstance(data, Mapping):
        raise FroniusDataError(f"{source}.Body.Data is missing or invalid")
    return data


def _timestamp(payload: Mapping[str, Any]) -> str:
    head = payload.get("Head")
    value = head.get("Timestamp") if isinstance(head, Mapping) else None
    if not isinstance(value, str) or not value.strip():
        raise FroniusDataError("PowerFlow.Head.Timestamp is missing or invalid")
    return value


def _day_energy(payload: Optional[Mapping[str, Any]]) -> float:
    if payload is None:
        return 0.0
    data = _body(payload, "InverterRealtimeData")
    day_energy = data.get("DAY_ENERGY")
    if day_energy is None:
        return 0.0
    if not isinstance(day_energy, Mapping):
        raise FroniusDataError("DAY_ENERGY is invalid")
    values = day_energy.get("Values")
    if not isinstance(values, Mapping):
        raise FroniusDataError("DAY_ENERGY.Values is missing or invalid")

    total_wh = 0.0
    for inverter_id, value in values.items():
        if value is not None:
            total_wh += _finite_number(value, f"DAY_ENERGY.Values[{inverter_id!r}]")
    return total_wh / 1000.0


def _battery(_: Optional[Mapping[str, Any]]) -> Tuple[bool, float, float]:
    # No real storage field names or power direction have been verified yet.
    # Keeping this mapping isolated makes the future sign conversion local.
    return False, 0.0, 0.0


def _heat(_: Optional[Mapping[str, Any]]) -> Tuple[bool, float]:
    # The installed Ohmpilot currently returns {}. Its live power field must be
    # verified before a value can safely be mapped here.
    return False, 0.0


def normalize_live_data(
    power_flow: Mapping[str, Any],
    inverter_realtime: Optional[Mapping[str, Any]] = None,
    storage: Optional[Mapping[str, Any]] = None,
    ohmpilot: Optional[Mapping[str, Any]] = None,
) -> LiveData:
    """Convert Fronius payloads to the stable internal live-data model."""

    site = _body(power_flow, "PowerFlow").get("Site")
    if not isinstance(site, Mapping):
        raise FroniusDataError("PowerFlow.Body.Data.Site is missing or invalid")

    pv_w = _finite_number(site.get("P_PV"), "P_PV")
    load_w = _finite_number(site.get("P_Load"), "P_Load")
    grid_w = _finite_number(site.get("P_Grid"), "P_Grid")
    battery_available, battery_soc, battery_power = _battery(storage)
    heat_available, heat_power = _heat(ohmpilot)

    return LiveData(
        timestamp=_timestamp(power_flow),
        solar_power_kw=pv_w / 1000.0,
        house_power_kw=-load_w / 1000.0,
        heat_power_kw=heat_power,
        battery_soc_pct=battery_soc,
        battery_power_kw=battery_power,
        grid_power_kw=-grid_w / 1000.0,
        energy_today_kwh=_day_energy(inverter_realtime),
        battery_available=battery_available,
        heat_available=heat_available,
    )
