"""Validated local catalogues and renderer-independent variant layout."""
import hashlib
import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from string import Formatter

from .width import text_width_px

DIRECTORY = Path(__file__).with_name("content")
FACTORS = {"runtime": "power_w", "uses": "kwh_per_unit",
           "distance": "kwh_per_km", "quantity": "kwh_per_unit"}
FIELDS = set("average baseline count date day_label days delta_minutes delta duration energy high km kwh low month mwh n old_record pct quantity rank sunrise sunset sunshine threshold when years".split())


def positive(value):
    return type(value) in (float, int) and math.isfinite(value) and value > 0


def load(name, group):
    data = json.loads((DIRECTORY / (name + ".json")).read_text(encoding="utf8"))
    if data["version"] != 2 or data["locale"] != "de-CH":
        raise ValueError(name)
    rows = {}
    aliases = set()
    for row in data[group]:
        ident = row["id"]
        if not ident or ident in rows:
            raise ValueError(ident)
        variants = row["variants"]
        branches = variants if isinstance(variants, dict) else {"default": variants}
        fields = FIELDS
        if group == "energy":
            kind = row["type"]
            if not positive(row[FACTORS[kind]]) or not positive(row["min_kwh"]):
                raise ValueError(ident)
            maximum = row["max_kwh"]
            if maximum is not None and (not positive(maximum) or maximum < row["min_kwh"]):
                raise ValueError(ident)
            for alias in row["legacy_ids"]:
                if alias in aliases or alias in data["retired_legacy_ids"]:
                    raise ValueError(alias)
                aliases.add(alias)
            fields = {"energy", "when", {"runtime": "duration", "uses": "count",
                                        "distance": "km", "quantity": "quantity"}[kind]}
        for key, templates in branches.items():
            if not key or not templates or len(set(templates)) != len(templates):
                raise ValueError(ident)
            for template in templates:
                if not template.strip() or "\n" in template or "ß" in template:
                    raise ValueError(template)
                for _, var, spec, conv in Formatter().parse(template):
                    if var is not None and (var not in fields or spec or conv):
                        raise ValueError(template)
        rows[ident] = row
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
        (.1, .001, "Millisekunde", "Millisekunden"), (60, 1, "Sekunde", "Sekunden"),
        (3600, 60, "Minute", "Minuten"), (86400, 3600, "Stunde", "Stunden"),
        (365*86400, 86400, "Tag", "Tage"), (math.inf, 365*86400, "Jahr", "Jahre"),
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
        return self.row.get("semantics", {}).get(self.branch, self.row.get("semantic", self.ident))


def energy_candidate(row, anchor, actual, when="heute"):
    if not positive(anchor) or not positive(actual) or when not in ("heute", "gestern"):
        return None
    if not row["min_kwh"] <= anchor <= (row["max_kwh"] or math.inf):
        return None
    kind = row["type"]
    value = actual / row[FACTORS[kind]]
    values = dict(when=when, energy=number(actual, 2 if actual < 1 else 1))
    if kind == "runtime":
        values["duration"] = duration(value * 3600000)
    elif kind == "quantity":
        values["quantity"] = number(value, max(0, 1-math.floor(math.log10(value))))
    else:
        count = round(value, -max(0, math.floor(math.log10(value))-1))
        if round(count) < 1:
            return None
        values["count" if kind == "uses" else "km"] = number(count, 0)
    return Candidate(row["id"], row["family"], row, values)


def digest(*parts):
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest(), "big")


@lru_cache(maxsize=2048)
def wrap(text, limit):
    if text_width_px(text) <= limit:
        return text, ""
    choices = []
    for index, char in enumerate(text):
        if char != " ":
            continue
        left, right = text[:index], text[index+1:]
        a, b = text_width_px(left), text_width_px(right)
        if max(a, b) <= limit:
            penalty = 90 if left[-1].isdigit() and right.split()[0].strip(".,") in (
                "kWh", "W", "km", "%", "kg", "Minuten", "Stunden", "Uhr") else 0
            choices.append((abs(a-b) + penalty - 40*(left[-1] in ".,;:!?"), left, right))
    return min(choices)[1:] if choices else None


def render(candidate, slot, preferred=None):
    variants = candidate.row["variants"]
    if isinstance(variants, dict):
        variants = variants[candidate.branch]
    first = preferred if preferred is not None else digest(slot, candidate.ident, candidate.reference)
    for limit in (490, 510):
        for offset in range(len(variants)):
            index = (first + offset) % len(variants)
            text = variants[index].format_map(candidate.values)
            lines = wrap(text, limit)
            if lines:
                return index, lines
    return None
