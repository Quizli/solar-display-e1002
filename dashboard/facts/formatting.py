import math


def _decimal(value, places=1):
    return f"{value:.{places}f}".replace(".", ",")


def format_fact_number(value, unit_type="count"):
    """Format fact quantities without locale or platform dependencies."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("fact number must be finite")
    value = max(0.0, float(value))
    if unit_type in ("decimal", "energy"):
        # Energy/status values retain useful hundredths below one kWh.
        places = 2 if value < 1 else 1
        return _decimal(value, places)
    if value >= 1_000_000_000:
        return f"{_decimal(value / 1_000_000_000)} Milliarden"
    if value >= 1_000_000:
        return f"{_decimal(value / 1_000_000)} Millionen"
    if value < 10:
        return _decimal(value)
    if value < 1_000:
        rounded = round(value)
    elif value < 10_000:
        rounded = round(value / 10) * 10
    else:
        rounded = round(value / 100) * 100
    return f"{rounded:,}".replace(",", "’")
