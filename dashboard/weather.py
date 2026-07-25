"""Compact WMO weather-code mapping used by the dashboard."""

WEATHER_FALLBACK = "sunny"


def weather_icon_variant(weather_code):
    """Return the small-icon variant for an Open-Meteo/WMO weather code."""
    if isinstance(weather_code, bool) or not isinstance(weather_code, int):
        return WEATHER_FALLBACK
    if weather_code == 0:
        return "sunny"
    if weather_code == 1:
        return "mainly_clear"
    if weather_code == 2:
        return "partly_cloudy"
    if weather_code == 3:
        return "overcast"
    if weather_code in (45, 48):
        return "fog"
    if 51 <= weather_code <= 67 or 80 <= weather_code <= 82:
        return "rain"
    if 71 <= weather_code <= 77 or 85 <= weather_code <= 86:
        return "snow"
    if 95 <= weather_code <= 99:
        return "thunderstorm"
    return WEATHER_FALLBACK
