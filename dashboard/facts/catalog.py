from .formatting import (format_energy, format_fact_number, format_hours_as_days,
                         format_hours_as_years, format_large_count,
                         format_percent, format_seconds)
from .models import FactDefinition


def _energy_period(energy, period):
    return format_energy(energy), period


def render_d01(e, p):
    value = format_fact_number(e / .017, "approximate_count")
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{value} vollständige iPhone-Ladungen."


def render_d02(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{format_fact_number(e * 90)} km Instagram-Doomscrolling."


def render_k01(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{format_fact_number(e / .015)} Espressi."


def render_k03(e, p):
    energy, p = _energy_period(e, p)
    return f"Aus den {p} {energy} kWh könnten rund", f"{format_fact_number(e / .005)} Eiswürfel entstehen."


def render_k08(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{format_fact_number(e / .30)} Racletteportionen."


def render_m01(e, p):
    energy, p = _energy_period(e, p)
    return f"Für die {p} {energy} kWh müsste ein Mensch", f"rund {format_fact_number(e / .125)} Stunden Velo fahren."


def render_m03(e, p):
    energy, p = _energy_period(e, p)
    return f"Für die {p} {energy} kWh wären rund", f"{format_large_count(e * 14_400_000)} Hamsterrad-Runden nötig."


def render_v01(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{format_fact_number(e / .007)} km mit dem E-Bike."


def render_v03(e, p):
    energy, p = _energy_period(e, p)
    return f"Mit den {p} {energy} kWh käme ein Model 3", f"rund {format_fact_number(e / .13)} km weit."


def render_v07(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{format_fact_number(e / .03)} Liftfahrten vom UG ins 2. OG."


def render_v09(e, p):
    energy, p = _energy_period(e, p)
    return f"Mit den {p} {energy} kWh käme ein Zürcher Tram", f"rund {format_fact_number(e / 3.5)} km weit."


def render_v09_short(e, _p):
    energy = format_energy(e)
    return f"{energy} kWh bringen ein Zürcher Tram rund", f"{format_fact_number(e / 3.5)} km weit."


def render_h01(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh reichen für rund", f"{format_fact_number(e / .368)} Waschmaschinenladungen."


def render_u01(e, _p):
    energy = format_energy(e)
    return f"Eine Discokugel könnte mit {energy} kWh rund", f"{format_hours_as_years(e / .004)} Jahre rotieren."


def render_u02(e, _p):
    energy = format_energy(e)
    return f"Eine Schulglocke könnte mit {energy} kWh rund", f"{format_hours_as_days(e / .040)} Tage ununterbrochen läuten."


def render_x01(e, p):
    energy, p = _energy_period(e, p)
    return f"Das AKW Gösgen erzeugt die {p} {energy} kWh", f"in rund {format_seconds(e / 1_010_000 * 3600)} Sekunden."


def render_x03(e, p):
    energy, p = _energy_period(e, p)
    return f"Die {p} {energy} kWh entsprechen rund", f"{format_percent(e / 417 * 100)} % eines durchschnittlichen Blitzes."


def render_x11(e, p):
    energy, p = _energy_period(e, p)
    return f"Der LHC am CERN verbraucht die {p} {energy} kWh", f"in rund {format_seconds(e / 68_500 * 3600)} Sekunden."


def render_cmb07(e, _p):
    energy = format_energy(e)
    years = format_fact_number(e * 1.369)
    seconds = format_seconds(e / 1_010_000 * 3600)
    return (f"Für {energy} kWh bräuchte ein Goldhamster rund {years} Jahre.",
            f"Das AKW Gösgen benötigt rund {seconds} Sekunden.")


FACTS = (
    FactDefinition("D01", "digital", 1, 120, 1, render_d01),
    FactDefinition("D02", "digital", 5, 120, 1, render_d02),
    FactDefinition("K01", "kitchen", 1, 80, 1, render_k01),
    FactDefinition("K03", "kitchen", 1, 80, 1, render_k03),
    FactDefinition("K08", "kitchen", 5, 120, 1, render_k08),
    FactDefinition("M01", "motion", 10, 160, 1, render_m01),
    FactDefinition("M03", "motion", 5, 160, 1, render_m03),
    FactDefinition("V01", "mobility", 5, 160, 1, render_v01),
    FactDefinition("V03", "mobility", 10, 160, 1, render_v03),
    FactDefinition("V07", "mobility", 1, 80, 1, render_v07),
    FactDefinition("V09", "mobility", 25, 160, 1, render_v09, render_v09_short),
    FactDefinition("H01", "household", 1, 120, 1, render_h01),
    FactDefinition("U01", "curious", 5, 160, 1, render_u01),
    FactDefinition("U02", "curious", 5, 160, 1, render_u02),
    FactDefinition("X01", "large_scale", 100, 160, 1, render_x01),
    FactDefinition("X03", "large_scale", 25, 160, 1, render_x03),
    FactDefinition("X11", "large_scale", 50, 160, 1, render_x11),
    FactDefinition("CMB07", "combined", 50, 160, 1, render_cmb07),
)

FACTS_BY_ID = {fact.fact_id: fact for fact in FACTS}


def has_eligible_fact_for_energy(energy_kwh):
    """Whether at least one catalog fact admits this selection energy."""
    if energy_kwh is None:
        return False
    return any(fact.min_kwh <= energy_kwh <= fact.max_kwh for fact in FACTS)
