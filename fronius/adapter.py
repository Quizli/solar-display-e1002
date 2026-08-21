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


def _non_negative_energy(value: Any) -> Optional[float]:
    """Return a valid cumulative Wh value, otherwise mark it unavailable."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0:
        return None
    return result


def _total_energy(power_flow: Mapping[str, Any]) -> Optional[float]:
    data = _body(power_flow, "PowerFlow")
    site = data.get("Site")
    if isinstance(site, Mapping):
        site_total = _non_negative_energy(site.get("E_Total"))
        if site_total is not None:
            return site_total / 1000.0

    inverters = data.get("Inverters")
    if not isinstance(inverters, Mapping):
        return None
    totals = []
    for inverter in inverters.values():
        if isinstance(inverter, Mapping):
            total = _non_negative_energy(inverter.get("E_Total"))
            if total is not None:
                totals.append(total)
    return sum(totals) / 1000.0 if totals else None


def _battery(
    storage: Optional[Mapping[str, Any]], site: Mapping[str, Any]
) -> Tuple[bool, float, float]:
    """Normalize an enabled Fronius storage device and system battery power.

    Fronius reports positive ``P_Akku`` while the battery is discharging and
    negative values while it is charging.  The display model deliberately has
    the opposite convention, so the sign inversion belongs here at the adapter
    boundary and must not be repeated by renderers.
    """
    if storage is None:
        return False, 0.0, 0.0
    body = storage.get("Body")
    devices = body.get("Data") if isinstance(body, Mapping) else None
    if not isinstance(devices, Mapping):
        return False, 0.0, 0.0

    battery_power_w = site.get("P_Akku")
    if (isinstance(battery_power_w, bool)
            or not isinstance(battery_power_w, (int, float))
            or not math.isfinite(float(battery_power_w))):
        return False, 0.0, 0.0

    for device in devices.values():
        controller = device.get("Controller") if isinstance(device, Mapping) else None
        if not isinstance(controller, Mapping):
            continue
        enabled = controller.get("Enable")
        if (isinstance(enabled, bool)
                or not isinstance(enabled, (int, float))
                or not math.isfinite(float(enabled))
                or float(enabled) != 1.0):
            continue
        soc = controller.get("StateOfCharge_Relative")
        if (isinstance(soc, bool) or not isinstance(soc, (int, float))
                or not math.isfinite(float(soc)) or not 0 <= float(soc) <= 100):
            continue
        return True, float(soc), -float(battery_power_w) / 1000.0

    return False, 0.0, 0.0


def _heat(payload: Optional[Mapping[str, Any]]) -> Tuple[bool, float]:
    """Sum confirmed, optional Ohmpilot real-power values."""
    if payload is None:
        return False, 0.0
    body = payload.get("Body")
    data = body.get("Data") if isinstance(body, Mapping) else None
    if not isinstance(data, Mapping):
        return False, 0.0

    powers = []
    for device in data.values():
        value = (
            device.get("PowerReal_PAC_Sum")
            if isinstance(device, Mapping)
            else None
        )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        power_w = float(value)
        if math.isfinite(power_w) and power_w >= 0:
            powers.append(power_w)
    return (True, sum(powers) / 1000.0) if powers else (False, 0.0)


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
    battery_available, battery_soc, battery_power = _battery(storage, site)
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
        energy_total_kwh=_total_energy(power_flow),
        battery_available=battery_available,
        heat_available=heat_available,
    )
