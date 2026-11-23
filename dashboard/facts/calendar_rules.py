from datetime import date, datetime, time, timedelta
from solar_data.timezones import ZURICH
from .editorial import CALENDAR, Candidate

# Local dates verified with PyEphem 4.2.1 next_{spring,summer,autumn,winter}_*
# for January 1 UTC, then converted from UTC to Europe/Zurich.
# https://rhodesmill.org/pyephem/quick.html#equinoxes-solstices
SEASONS = {
    2026: ("03-20", "06-21", "09-23", "12-21"),
    2027: ("03-20", "06-21", "09-23", "12-22"),
}
EVENT_IDS = ("SPRING_EQUINOX", "SUMMER_SOLSTICE", "AUTUMN_EQUINOX", "WINTER_SOLSTICE")
EASTER_OFFSETS = {
    "GOOD_FRIDAY": -2,
    "EASTER": 0,
    "EASTER_MONDAY": 1,
    "ASCENSION": 39,
    "WHIT_MONDAY": 50,
}


def easter(year):
    # Meeus/Jones/Butcher Gregorian computus.
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (b - (b + 8) // 25 + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def calendar_candidates(day, morning):
    stamp = day.strftime("%m-%d")
    midnight = datetime.combine(day, time.min, ZURICH)
    change = (midnight + timedelta(days=1)).utcoffset() - midnight.utcoffset()
    for ident, row in CALENDAR.items():
        if row["phase"] == "morning" and not morning:
            continue
        if ident in EASTER_OFFSETS:
            matches = day == easter(day.year) + timedelta(days=EASTER_OFFSETS[ident])
        elif ident in EVENT_IDS:
            matches = (
                day.year in SEASONS
                and SEASONS[day.year][EVENT_IDS.index(ident)] == stamp
            )
        elif ident.startswith("DST_"):
            matches = (
                change.total_seconds() > 0
                if ident == "DST_START"
                else change.total_seconds() < 0
            )
        else:
            matches = stamp == row["date_rule"][:5]
        if matches:
            yield Candidate(ident, "calendar", row, reference=day.isoformat())
