from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Mapping, Optional, Tuple


Lines = Tuple[str, str]


@dataclass(frozen=True)
class FactContext:
    now_local: datetime
    sunrise: Optional[datetime]
    sunset: Optional[datetime]
    today_energy_kwh: Optional[float]
    yesterday_energy_kwh: Optional[float]
    solar_power_kw: Optional[float]
    weather_code: Optional[int]
    selection_energy_kwh: Optional[float] = None
    previous_hour_selection_energy_kwh: Optional[float] = None
    selection_energy_by_hour: Mapping[str, float] = field(default_factory=dict)
    selection_bucket_by_hour: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Story:
    fact_id: str
    family: str
    line_1: str
    line_2: str
    phase: str
    energy_period: Optional[str]
    energy_kwh: Optional[float] = None
    used_short_template: bool = False
    line_1_width_px: float = 0
    line_2_width_px: float = 0
    selection_energy_kwh: Optional[float] = None
    previous_hour_selection_energy_kwh: Optional[float] = None
    selection_hour: Optional[str] = None
    selection_bucket_start: Optional[str] = None
    selection_persisted: bool = False
    selection_source: str = "none"


@dataclass(frozen=True)
class FactDefinition:
    fact_id: str
    family: str
    min_kwh: float
    max_kwh: float
    weight: float
    render: Callable[[float, str], Lines]
    render_short: Optional[Callable[[float, str], Lines]] = None
