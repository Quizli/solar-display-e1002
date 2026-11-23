from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional, Tuple


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

    sunshine_hours: Optional[float] = None
    previous_daylight_seconds: Optional[float] = None
    has_buckets: bool = False


@dataclass(frozen=True)
class Story:
    fact_id: Optional[str]
    family: Optional[str]
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
    variant: Optional[int] = None
    branch: str = "default"
    semantic: str = ""
    event_key: Optional[str] = None
    historical_fact: bool = False
    historical_reference_day: Optional[str] = None
    historical_baseline_kwh: Optional[float] = None
