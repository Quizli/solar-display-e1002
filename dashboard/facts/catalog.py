from .formatting import format_fact_number as number
from .models import FactDefinition


def _fact(fact_id, family, factor, noun, detail, short_noun=None, min_kwh=.1, max_kwh=10000):
    def normal(energy, period):
        amount = number(energy * factor)
        return (f"Der {period} Solarertrag entspricht {amount} {noun}.", detail)
    def short(energy, period):
        amount = number(energy * factor)
        return (f"{amount} {short_noun or noun} dank dem {period} Solarertrag.", detail)
    return FactDefinition(fact_id, family, min_kwh, max_kwh, 1.0, normal, short)


# Fixed physical/everyday approximations. They are intentionally code constants:
# facts are editorial content, not deployment configuration.
FACTS = (
    _fact("D01", "digital", 65, "iPhone-Ladungen", "Genug Energie für viele Nachrichten unterwegs."),
    _fact("D02", "digital", 500, "Stunden Instagram-Doomscrolling", "Der Daumen hätte dabei einiges zu tun.", "Stunden Doomscrolling"),
    _fact("K01", "kitchen", 10, "Espressi", "Die passende Menge für eine sehr lange Kaffeepause."),
    _fact("K03", "kitchen", 1250, "Eiswürfeln", "Damit bliebe manches Sommergetränk kühl."),
    _fact("K08", "kitchen", 1.25, "Racletteportionen", "Geschmolzener Käse braucht erstaunlich viel Strom."),
    _fact("M01", "motion", 5, "Stunden menschlicher Veloleistung", "Beine müssten dafür kräftig in die Pedale treten.", "Velostunden"),
    _fact("M03", "motion", 1800, "Hamsterrad-Runden", "Ein Goldhamster wäre damit lange beschäftigt."),
    _fact("V01", "mobility", 50, "E-Bike-Kilometern", "Damit reicht es weit über die Stadtgrenze hinaus."),
    _fact("V03", "mobility", 6, "Kilometern im Tesla Model 3", "Elektrisch unterwegs mit Energie vom Dach.", "Tesla-Kilometern"),
    _fact("V07", "mobility", 25, "Liftfahrten vom UG bis ins 2. OG", "Treppensteigen spart diese Energie ganz ein.", "Liftfahrten UG–2. OG"),
    _fact("V09", "mobility", .22, "Kilometern mit einem Zürcher Tram", "Ein ganzes Tram benötigt entsprechend mehr Energie.", "Zürcher Tramkilometern"),
    _fact("H01", "household", 1.4, "Waschmaschinenladungen", "Saubere Wäsche direkt mit Sonnenstrom."),
    _fact("U01", "curious", 100, "Stunden Discokugellicht", "Zeit für eine ziemlich ausdauernde Party."),
    _fact("U02", "curious", 3600, "Sekunden Schulglocke", "Das wären ausgesprochen viele Pausenzeichen."),
    _fact("X01", "large_scale", 0.000001, "Sekunden im AKW Gösgen", "Ein Grosskraftwerk erzeugt diese Menge blitzschnell.", "AKW-Sekunden"),
    _fact("X03", "large_scale", .00025, "durchschnittlichen Blitzenergien", "Ein Blitz setzt seine Energie in einem Moment frei.", "Blitzenergien"),
    _fact("X11", "large_scale", .000008, "Sekunden LHC-Betrieb am CERN", "Teilchenphysik spielt energetisch in einer anderen Liga.", "LHC-Sekunden"),
    _fact("CMB07", "combined", 1800, "Hamsterrad-Runden", "Das AKW Gösgen schafft dieselbe Energie fast augenblicklich."),
)
