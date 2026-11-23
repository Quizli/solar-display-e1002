# Insights v2 validation

The two review stages are the catalogue/formatting/storage commit followed by the
publisher, dynamic selection and simulation commit. All five editorial files live
in [dashboard/facts/content](../../dashboard/facts/content/). They contain 76 energy
concepts (152 variants), 14 morning types, 17 historical types, 26 calendar rules
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

## Sample stories

- `SUNSHINE_OUTLOOK` · 2026-06-02 01:05 · Heute werden 11,0 Sonnenstunden erwartet. Viel Zeit für einen sonnigen Tagesverlauf.
- `HIST_7DAY_TOTAL` · 2026-12-01 00:05 · Die letzten 7 vollständig erfassten Tage brachten zusammen 69,0 kWh Solarstrom.
- `HIGH_YIELD_COUNT` · 2026-06-04 00:05 · Die Statistik zählt nun 25 vollständig erfasste Tage mit mehr als 100 kWh.
- `goesgen` · 2026-06-02 17:05 · Bei Nennleistung würde das AKW Gösgen 85,1 kWh von heute in rund 0,3 Sekunden erzeugen.

## Acceptance checks

- Python 3.12 and Node 24 ran all 213 tests, including 16 JavaScript tests invoked by unittest. All passed with zero skips.
- The fixture-to-SQLite publisher smoke test produced SVG and public JSON. Nginx served both outputs, HTML, CSS and JavaScript through the repository routes.
- Headless Chromium rendered the actual 800×480 SVG. The geometry check measured 1,941 distinct lines including wide-glyph probes at the 490 and 510 px limits. Maximum actual advance was 499.8 px. The web view joined the published lines at 320, 390 and 1280 px, with an empty second field.
- The populated-copy migration retained measurements and legacy selections. Two concurrent publishers observed one lifetime event. Query-count tests exercised 30, 300 and 1,000 days.
- The October 2026 DST replay had 25 slots. The collision scenario rotated the 100th operating day with the first MWh. A reset quarantined the meter. The missing-data scenario displayed the missing status throughout. The zero-production day ended with `ZERO_DAY_COMPLETE`.

## Editorial details and limits

The second LHC variant includes the period (“von heute/gestern”). The formatter
inflects singular uses and forecast hours. Appliance, generation and transport
equivalents use the supplied assumptions. The runtime reads local JSON once.

The verified Zurich season table covers 2026 and 2027. Forecast comparison begins
after a successful cache rollover. Real replay has the retained raw samples and
final stored buckets; expired raw data, past forecast responses and the original
arrival times of later corrections cannot be recovered. The seasonal runs are
hourly selection checks. Separate tests exercise intra-hour overrides. Browser
geometry was measured on the cloud fonts; the physical E1002 was unavailable.

See [operations](../OPERATIONS.md) for optional commissioning settings, meter
continuity checks, SQLite backup and rollback.
