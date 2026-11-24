"""IDs and variables provided by the typed v2 handlers."""

ENERGY_IDS = set(
    """sewing_machine vacuum hair_dryer iron washing_machine dishwasher dryer fridge fan electric_blanket espresso kettle toast raclette soup oven_pizza popcorn_machine mixer waffle_iron ice_cubes phone_charge tablet_charge laptop wifi_router gaming_pc small_computer ereader_charge powerbank_charge pc_workstation radio disco_ball lava_lamp pinball arcade projector model_railway game_table console_tv electric_piano 3d_printer school_bell aquarium_pump fairy_lights led museum_spot microscope ebike electric_car escooter cargo_bike electric_bus tram lift_trip cycling_generator water_warm ice_melt lift_tonne pump_water goesgen lhc wind_turbine old_lightbulb streetlamp traffic_signal electric_toothbrush air_dancer snow_globe clock electric_heater elevator_motor robot_mower chocolate_energy sprint_car lift_person_alps drone battery_equivalent""".split()
)
CALENDAR_IDS = set(
    """NEW_YEAR BERCHTOLD GOOD_FRIDAY EASTER EASTER_MONDAY LABOUR_DAY ASCENSION WHIT_MONDAY NATIONAL_DAY CHRISTMAS_EVE CHRISTMAS BOXING_DAY NEW_YEAR_EVE LEAP_DAY DST_START DST_END SPRING_EQUINOX SUMMER_SOLSTICE AUTUMN_EQUINOX WINTER_SOLSTICE NATIONAL_DAY_EVE CHRISTMAS_EVE_EVE DECEMBER_START MARCH_START JUNE_START SEPTEMBER_START""".split()
)
MORNING_FIELDS = {
    "YESTERDAY_RECAP": {
        "neutral": "energy",
        "higher": "energy pct",
        "lower": "delta energy",
        "similar": "baseline energy",
    },
    "YESTERDAY_RANK": {"high": "energy rank", "low": "energy rank"},
    "RECORD_AFTERGLOW": {"default": "energy old_record"},
    "SUNSHINE_OUTLOOK": {
        "very_low": "sunshine",
        "low": "sunshine",
        "medium": "sunshine",
        "high": "sunshine",
        "very_high": "sunshine",
    },
    "WEATHER_OUTLOOK": {
        "sunny": "",
        "mainly_clear": "",
        "partly_cloudy": "",
        "overcast": "",
        "fog": "",
        "rain": "",
        "snow": "",
        "thunderstorm": "",
    },
    "DAYLIGHT": {"default": "duration sunrise sunset"},
    "DAYLIGHT_CHANGE": {"longer": "delta_minutes", "shorter": "delta_minutes"},
    "SEASONAL_DIRECTION": {"longer": "", "shorter": "", "near_solstice": ""},
    "STREAK_RECAP": {"running": "days", "ended": "days"},
    "RECENT_TREND": {"higher": "baseline energy pct", "lower": "baseline energy pct"},
    "NIGHT_STATUS": {"sunrise_known": "sunrise", "generic": ""},
    "WAITING_STATUS": {"sunrise_known": "sunrise", "late": ""},
    "TECH_CONTEXT": {"default": "", "empty_chart": ""},
    "ZERO_DAY_COMPLETE": {"default": ""},
}
HISTORY_FIELDS = {
    "HIST_YESTERDAY": {
        "higher": "baseline delta energy pct",
        "lower": "baseline delta energy pct",
        "similar": "baseline energy",
        "absolute": "baseline energy",
    },
    "HIST_AVERAGE": {
        "higher": "baseline day_label delta energy n pct",
        "lower": "baseline day_label delta energy n pct",
        "similar": "baseline day_label energy n",
    },
    "HIST_RECENT_RANK": {
        "high": "date day_label energy rank",
        "low": "date day_label energy rank",
    },
    "HIST_3DAY_TREND": {
        "higher": "baseline energy pct",
        "lower": "baseline energy pct",
    },
    "HIST_7DAY_TOTAL": {"default": "average energy"},
    "HIST_30DAY_TOTAL": {"default": "average energy"},
    "HIST_LAST_WEEK": {"default": "average energy"},
    "HIST_LAST_MONTH": {"default": "average energy month"},
    "HIST_MONTH_TO_DATE": {"default": "energy month"},
    "HIST_RECENT_RANGE": {"default": "delta high low"},
    "HIST_RECENT_BEST": {"default": "date energy"},
    "HIST_THRESHOLD_COUNT": {"default": "count threshold"},
    "HIST_RECORD_AGE": {"default": "date days energy"},
    "HIST_STREAK": {"default": "days"},
    "HIST_STREAK_END": {"default": "days energy"},
    "HIST_WEEKDAY": {
        "higher": "delta energy",
        "lower": "delta energy",
        "similar": "baseline energy",
        "absolute": "baseline energy",
    },
    "HIST_LOW_YIELD": {"default": "count"},
}
MILESTONES_FIELDS = {
    "OPERATING_DAY": {"default": "days", "d100": "", "d1000": ""},
    "ANNIVERSARY": {"first": "", "later": "years"},
    "LIFETIME_ENERGY": {"default": "kwh mwh", "mwh1": "", "mwh10": "", "mwh100": ""},
    "DAILY_RECORD": {"default": "energy old_record"},
    "PRODUCTION_BARRIER": {"default": "threshold"},
    "HIGH_YIELD_COUNT": {"default": "count", "c10": ""},
    "HIGH_YIELD_STREAK": {"default": "days", "d3": ""},
    "STREAK_RECORD": {"default": "days"},
}
HANDLERS = {
    "morning": MORNING_FIELDS,
    "history": HISTORY_FIELDS,
    "milestones": MILESTONES_FIELDS,
}
