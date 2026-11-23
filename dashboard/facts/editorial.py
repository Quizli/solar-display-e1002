"""Validated local catalogues and renderer-independent variant layout."""

import hashlib
import json
import math
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from string import Formatter

from .schema import CALENDAR_IDS, ENERGY_IDS, HANDLERS
from .width import text_width_px

DIRECTORY = Path(__file__).with_name("content")
FACTORS = {
    "runtime": "power_w",
    "uses": "kwh_per_unit",
    "distance": "kwh_per_km",
    "quantity": "kwh_per_unit",
}
PREVIEW_COUNTS = ContextVar("preview_counts", default=None)


def count_preview(reason):
    counts = PREVIEW_COUNTS.get()
    if counts is not None:
        counts[reason] += 1


def positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def load(name, group):
    data = json.loads((DIRECTORY / (name + ".json")).read_text(encoding="utf8"))
    if data["version"] != 2 or data["locale"] != "de-CH":
        raise ValueError(name)
    rows = {}
    aliases = set()
    seen_templates = set()
    expected = (
        ENERGY_IDS
        if group == "energy"
        else CALENDAR_IDS
        if group == "rules"
        else set(HANDLERS[name])
    )
    for row in data[group]:
        ident = row["id"]
        if ident not in expected or ident in rows:
            raise ValueError(ident)
        variants = row["variants"]
        branches = variants if isinstance(variants, dict) else {"default": variants}
        if group in ("rules", "energy") and not isinstance(variants, list):
            raise ValueError(ident)
        if name in ("morning", "calendar", "milestones") and (
            type(row["priority"]) != int or not 0 < row["priority"] <= 100
        ):
            raise ValueError(ident)
        fields = set()
        if name in HANDLERS and set(branches) != set(HANDLERS[name][ident]):
            raise ValueError(ident)
        for threshold_key in ("thresholds", "thresholds_kwh"):
            if threshold_key in row:
                cuts = row[threshold_key]
                if (
                    not cuts
                    or not all(positive(x) for x in cuts)
                    or cuts
                    != sorted(set(cuts), reverse=ident == "HIST_THRESHOLD_COUNT")
                ):
                    raise ValueError(ident)
        if group == "rules" and (
            row["phase"] not in ("day", "morning")
            or row["classification"]
            not in ("ZH_statutory", "ZH_customary", "calendar", "cultural", "astronomy")
        ):
            raise ValueError(ident)
        if group == "energy":
            if (
                row["family"]
                not in "household food digital leisure curious craft lighting science mobility large_scale infrastructure local".split()
            ):
                raise ValueError(ident)
            kind = row["type"]
            if not positive(row[FACTORS[kind]]) or not positive(row["min_kwh"]):
                raise ValueError(ident)
            maximum = row["max_kwh"]
            if maximum is not None and (
                not positive(maximum) or maximum < row["min_kwh"]
            ):
                raise ValueError(ident)
            for alias in row["legacy_ids"]:
                if alias in aliases or alias in data["retired_legacy_ids"]:
                    raise ValueError(alias)
                aliases.add(alias)
            fields = {
                "energy",
                "when",
                {
                    "runtime": "duration",
                    "uses": "count",
                    "distance": "km",
                    "quantity": "quantity",
                }[kind],
            }
        for key, templates in branches.items():
            if name in HANDLERS:
                fields = set(HANDLERS[name][ident][key].split())
            if (
                not key
                or not isinstance(templates, list)
                or not templates
                or len(set(templates)) != len(templates)
            ):
                raise ValueError(ident)
            for template in templates:
                if template in seen_templates:
                    raise ValueError(template)
                seen_templates.add(template)
                if not template.strip() or "\n" in template or "ß" in template:
                    raise ValueError(template)
                for _, var, spec, conv in Formatter().parse(template):
                    if var is not None and (var not in fields or spec or conv):
                        raise ValueError(template)
        rows[ident] = row
    if set(rows) != expected:
        raise ValueError(f"Missing handlers in {name}: {expected - set(rows)}")
    return rows


ENERGY = load("energy-catalog", "energy")
MORNING = load("morning", "stories")
HISTORY = load("history", "stories")
CALENDAR = load("calendar", "rules")
EVENTS = load("milestones", "events")
ALIASES = {alias: ident for ident, row in ENERGY.items() for alias in row["legacy_ids"]}


def number(value, places=1):
    return f"{value:,.{places}f}".replace(",", "’").replace(".", ",")


def duration(seconds):
    for limit, scale, singular, plural in (
        (0.1, 0.001, "Millisekunde", "Millisekunden"),
        (60, 1, "Sekunde", "Sekunden"),
        (3600, 60, "Minute", "Minuten"),
        (86400, 3600, "Stunde", "Stunden"),
        (365 * 86400, 86400, "Tag", "Tage"),
        (math.inf, 365 * 86400, "Jahr", "Jahre"),
    ):
        if seconds < limit:
            amount = seconds / scale
            precision = max(0, 1 - math.floor(math.log10(amount))) if amount < 10 else 0
            label = number(amount, precision)
            if "," in label:
                label = label.rstrip("0").rstrip(",")
            return label + " " + (singular if label == "1" else plural)


@dataclass(frozen=True)
class Candidate:
    ident: str
    family: str
    row: dict
    values: dict = field(default_factory=dict)
    branch: str = "default"
    reference: str = ""
    meta: dict = field(default_factory=dict)

    @property
    def semantic(self):
        return self.row.get("semantics", {}).get(
            self.branch, self.row.get("semantic", self.ident)
        )


def energy_candidate(row, anchor, actual, when="heute"):
    if not positive(anchor) or not positive(actual) or when not in ("heute", "gestern"):
        count_preview("ineligible_energy")
        return None
    if not row["min_kwh"] <= anchor <= (row["max_kwh"] or math.inf):
        count_preview("ineligible_energy")
        return None
    kind = row["type"]
    value = actual / row[FACTORS[kind]]
    values = dict(when=when, energy=number(actual, 2 if actual < 1 else 1))
    if kind == "runtime":
        values["duration"] = duration(value * 3600000)
    elif kind == "quantity":
        values["quantity"] = number(value, max(0, 1 - math.floor(math.log10(value))))
    else:
        count = round(value, -max(0, math.floor(math.log10(value)) - 1))
        if round(count) < 1:
            count_preview("ineligible_energy")
            return None
        values["count" if kind == "uses" else "km"] = number(count, 0)
    return Candidate(row["id"], row["family"], row, values)


def digest(*parts):
    return int.from_bytes(
        hashlib.sha256("|".join(map(str, parts)).encode()).digest(), "big"
    )


@lru_cache(maxsize=2048)
def wrap(text, limit):
    if text_width_px(text) <= limit:
        return text, ""
    choices = []
    for index, char in enumerate(text):
        if char != " ":
            continue
        left, right = text[:index], text[index + 1 :]
        a, b = text_width_px(left), text_width_px(right)
        if max(a, b) <= limit:
            penalty = (
                90
                if left[-1].isdigit()
                and right.split()[0].strip(".,")
                in ("kWh", "W", "km", "%", "kg", "Minuten", "Stunden", "Uhr")
                else 0
            )
            choices.append(
                (abs(a - b) + penalty - 40 * (left[-1] in ".,;:!?"), left, right)
            )
    return min(choices)[1:] if choices else None


SINGULAR = dict(
    Waschgänge="Waschgang",
    Ladungen="Ladung",
    Spülgänge="Spülgang",
    Tumblerladungen="Tumblerladung",
    Kühlschranktage="Kühlschranktag",
    Tage="Tag",
    Litern="Liter",
    Racletteportionen="Racletteportion",
    Portionen="Portion",
    Suppenportionen="Suppenportion",
    Ofengänge="Ofengang",
    Hübe="Hub",
    Aufstiege="Aufstieg",
)
SINGULAR.update({"Eco-Gänge": "Eco-Gang", "Pizza-Backrunden": "Pizza-Backrunde"})


def render(candidate, slot, preferred=None):
    variants = candidate.row["variants"]
    if isinstance(variants, dict):
        variants = variants[candidate.branch]
    first = (
        preferred
        if preferred is not None
        else digest(slot, candidate.ident, candidate.reference)
    )
    for limit in (490, 510):
        for offset in range(len(variants)):
            index = (first + offset) % len(variants)
            template = variants[index]
            if (
                candidate.ident == "HIST_STREAK_END"
                and candidate.values["energy"] == "100,0"
                and "darunter" in template
            ):
                continue
            if candidate.values.get("count") == "1":
                for plural, singular in SINGULAR.items():
                    template = template.replace(
                        "{count} " + plural, "{count} " + singular
                    )
                template = template.replace(
                    "wären rund {count} Spülgang", "wäre rund {count} Spülgang"
                )
                template = template.replace("wären mit", "wäre mit").replace(
                    "liessen sich rund {count} Suppenportion",
                    "liesse sich rund {count} Suppenportion",
                )
            text = template.format_map(candidate.values)
            if (
                candidate.ident == "SUNSHINE_OUTLOOK"
                and candidate.values.get("sunshine") == "1,0"
            ):
                text = text.replace("1,0 Sonnenstunden", "1 Sonnenstunde").replace(
                    "1,0 Stunden Sonnenschein", "1 Stunde Sonnenschein"
                )
                text = text.replace("sind nur 1", "ist nur 1").replace(
                    "stehen nur 1", "steht nur 1"
                )
                text = text.replace("Sonnenstunde stehen", "Sonnenstunde steht")
            lines = wrap(text, limit)
            if lines:
                return index, lines
    count_preview("overflow")
    return None
