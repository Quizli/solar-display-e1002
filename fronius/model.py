from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class LiveData:
    """Fronius-independent values consumed by future application layers."""

    timestamp: str
    solar_power_kw: float
    house_power_kw: float
    heat_power_kw: float
    battery_soc_pct: float
    battery_power_kw: float
    grid_power_kw: float
    energy_today_kwh: float
    energy_total_kwh: Optional[float]
    battery_available: bool
    heat_available: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
