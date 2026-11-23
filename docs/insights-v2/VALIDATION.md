# Insights v2 validation

The two review stages are the catalogue/formatting/storage commit followed by the
publisher, dynamic selection and simulation commit. All five editorial files live
in [dashboard/facts/content](../../dashboard/facts/content/). They contain 76 energy
concepts (152 variants, with explicit singular forms for all 19 count-based concepts), 14 morning types, 17 historical types, 26 calendar rules
and eight milestone types. The selector recognises 27 legacy aliases.

## Seasonal simulations

Both runs used synthetic measurements and synthetic forecasts. Each starts with
40 prior calendar days of five-minute buckets and samples the production selector
at five minutes past each real hour in Europe/Zurich. No private solar database
was available in the checkout. Replay opens the supplied source read-only, copies
only samples and completed buckets available by the simulated instant, and keeps
selection and event state in memory.

```bash
python -m dashboard preview-facts --from 2026-06-01 --to 2026-06-30 --scenario sunny-summer
python -m dashboard preview-facts --from 2026-12-01 --to 2026-12-30 --scenario overcast-winter
```

| Result | June | December |
| --- | ---: | ---: |
| Hourly slots | 720 | 720 |
| Empty insights (absent ID or blank statement) | 0 | 0 |
| Missing-data status slots | 0 | 0 |
| Stale-data status slots | 0 | 0 |
| Distinct shared insight IDs | 101 | 92 |
| Distinct ID/branch/variant combinations | 199 | 178 |
| Historical tile types | 15 | 14 |
| Longest ordinary consecutive repeat (slots) | 1 | 1 |
| Context/history fallbacks | 26 | 7 |
| Energy candidates rejected by eligibility | 2376 | 1770 |
| Candidates rejected for overflow | 0 | 0 |
| Median distance between repeated ordinary IDs (hours) | 100.0 | 121.0 |

The repetition metrics exclude pinned milestones. The fallback counts include
the qualified history shown at first light when power reaches 0.1 kW before the
measured yield can support an energy equivalent. Both runs had a statement in
every slot. Full histograms and statements are emitted by the commands above.

| Shared insight family | June slots | December slots |
| --- | ---: | ---: |
| calendar | 3 | 10 |
| craft | 7 | 6 |
| curious | 64 | 58 |
| digital | 58 | 54 |
| food | 63 | 59 |
| history | 26 | 7 |
| household | 70 | 65 |
| infrastructure | 18 | 18 |
| large_scale | 19 | 16 |
| leisure | 52 | 47 |
| lighting | 27 | 23 |
| local | 6 | 6 |
| milestone | 72 | 0 |
| mobility | 44 | 40 |
| morning | 133 | 259 |
| science | 58 | 52 |

## Cold start and data quality

```bash
python -m dashboard preview-facts --from 2026-06-02 --to 2026-06-02 --scenario cold-start
```

The cold-start replay begins with an empty SQLite database at Zurich midnight.
It writes five-minute buckets as they close, with measurable PV at dawn and
a daily meter in 0.05 kWh steps. It evaluates the production publisher hourly
and every five minutes across first light. All 35 evaluations returned fresh,
nonempty insights. There were 23 distinct IDs, three technical fallbacks,
zero eligible historical tile types, zero overflows and zero missing or stale
statuses.

| Zurich time | Display yield (kWh) | Shared insight |
| --- | ---: | --- |
| 05:05 | 0 | `TECH_CONTEXT` |
| 05:10 | 0.0133 (integrated) | `TECH_CONTEXT` |
| 05:15 | 0.05 | `TECH_CONTEXT` |
| 05:20 | 0.10 | `robot_mower` |

At 05:20 the publisher rendered “0,10 kWh von heute würden einen 50-W-Mähroboter rund 2 Stunden beschäftigen.”
The nine consecutive evaluations of this ID cover one hourly selection sampled at five-minute intervals.
The isolated missing-data replay produced 24 `MISSING` statuses and zero empty insights.
The single-snapshot regression replay also checked `MISSING` before the first reading and `STALE` after it aged out.

## Sample stories

- `SUNSHINE_OUTLOOK` · 2026-06-02 01:05 · Heute werden 11,0 Sonnenstunden erwartet. Viel Zeit für einen sonnigen Tagesverlauf.
- `HIST_7DAY_TOTAL` · 2026-12-01 00:05 · Die letzten 7 vollständig erfassten Tage brachten zusammen 69,0 kWh Solarstrom.
- `HIGH_YIELD_COUNT` · 2026-06-04 00:05 · Die Statistik zählt nun 25 vollständig erfasste Tage mit mehr als 100 kWh.
- `goesgen` · 2026-06-02 17:05 · Bei Nennleistung würde das AKW Gösgen 85,1 kWh von heute in rund 0,3 Sekunden erzeugen.

## Acceptance checks

- `python -m unittest discover -s tests -v` passed all 221 tests with zero skips using Python 3.12.14 and Node 24.22.3. This includes 16 Node-backed JavaScript tests.
- The fixture-to-SQLite publisher smoke test produced SVG and public JSON. Nginx served both outputs, HTML, CSS and JavaScript through the repository routes.
- Headless Chromium rendered the actual 800×480 SVG. The geometry check measured 1,989 distinct lines including both singular forms for every count-based concept and wide-glyph probes at the 490 and 510 px limits. Maximum actual advance was 499.8 px. The web view joined the published lines at 320, 390 and 1280 px, with an empty second field.
- The populated-copy migration retained measurements and legacy selections. Two concurrent publishers observed one lifetime event. Query-count tests exercised 30, 300 and 1,000 days.
- The October 2026 DST replay had 25 slots. The collision scenario rotated the 100th operating day with the first MWh. A reset quarantined the meter. The missing-data scenario displayed the missing status throughout. The zero-production day ended with `ZERO_DAY_COMPLETE`.

## Review regressions and suite counts

- A fresh active panel with 0, 0.05 or unavailable kWh can render a curated chart explanation without persisting an energy selection. The tests cover a new installation and a day that used its shared historical fallback earlier. A complete, covered zero-production day reached `ZERO_DAY_COMPLETE` in all eight evening slots. Partial integration and unavailable yield use technical context.
- Running events use the verified cumulative yield. A corrected 142 kWh record retained its identity and displayed 125 kWh while the highest valid barrier became 120 kWh. At 90 kWh both pins cleared. After reopening the database, 100.13 kWh stayed below the frozen 100.04 + 0.1 kWh record threshold. A genuine rise to 142.1 kWh restored the original event keys. A publisher-level test also introduced a verified counter correction while an aggregate still read 142 kWh. The corrected 90 kWh display had no event, and the original aggregate remained intact.
- Sunrise 06:24 and sunset 21:14 produced “14 Stunden 50 Minuten”. Spring and autumn clock-change tests used elapsed UTC minutes. Each of the 38 singular forms rendered at its requested variant index and passed the two-line width check.
- Test discovery counted 229 methods on the pre-v2 base (`ea44c58`) and 213 on the reviewed implementation (`de7a6e3`). The v2 selector replaced assertions about legacy IDs, copy, and all-day yesterday pins with catalogue, phase, event and independent history checks. The review adds eight regression tests. The measurement, chart, storage and 0.5 kW threshold regression tests were unchanged by the review fixes.

| Test module | Pre-v2 | Reviewed v2 | After corrections |
| --- | ---: | ---: | ---: |
| Fact selection | 30 | 17 | 24 |
| Historical comparisons | 28 | 20 | 20 |
| Editorial and migration | 0 | 3 | 4 |
| Sun data | 11 | 12 | 12 |
| Node-backed dashboard | 15 | 16 | 16 |
| Other modules | 145 | 145 | 145 |
| **Total** | **229** | **213** | **221** |

## Editorial details and limits

The second LHC variant includes the period (“von heute/gestern”). The formatter
inflects singular uses and forecast hours. Appliance, generation and transport
equivalents use the supplied assumptions. The runtime reads local JSON once.

The verified Zurich season table covers 2026 and 2027. Forecast comparison begins
after a successful cache rollover. Real replay has the retained raw samples and
final stored buckets; expired raw data, past forecast responses and the original
arrival times of later corrections cannot be recovered. The seasonal runs are
hourly selection checks. Separate tests exercise intra-hour overrides. Browser
geometry was measured on the cloud fonts. The physical E1002 was unavailable.
New daily record events store their full-precision baseline. For events written
by the earlier draft, migration uses the qualified historical maximum when it
matches the stored display value. If that history has changed, only the original
one-decimal baseline is recoverable.

See [operations](../OPERATIONS.md) for optional commissioning settings, meter
continuity checks, SQLite backup and rollback.
