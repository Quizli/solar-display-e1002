# solar-display-e1002
Solar dashboard for Seeed Studio reTerminal E1002 with Fronius integration

## Fronius-Live-Daten

Die erste Datenzugriffsschicht ist bewusst vom SVG-Renderer getrennt:

```text
Fronius Solar API v1 -> FroniusClient -> Adapter -> LiveData
```

`LiveData` verwendet folgende Vorzeichenkonventionen:

- `grid_power_kw > 0`: Einspeisung; `< 0`: Netzbezug
- `battery_power_kw > 0`: Laden; `< 0`: Batterie versorgt Verbraucher

Die bestätigten Fronius-Mappings sind `P_PV / 1000` für Solarleistung,
`-P_Load / 1000` für Hausverbrauch und `-P_Grid / 1000` für Netzleistung.
Die nicht-null Inverterwerte aus `DAY_ENERGY.Values` (System Scope) werden
summiert und von Wh nach kWh umgerechnet. Fehlt der Tageswert oder sind seine
Inverterwerte `null`, wird vorläufig `0.0` verwendet. Die Pflichtwerte `P_PV`,
`P_Load` und `P_Grid` sowie der Zeitstempel werden dagegen validiert: fehlende,
ungültige oder nicht endliche Werte brechen die Normalisierung ab, damit ein
API-Fehler nicht als echter Null-Snapshot erscheint.

Storage und Ohmpilot sind optional. Fehlende Antworten, `null` oder leere
`Data`-Objekte ergeben sichere Nullwerte mit `battery_available=false` bzw.
`heat_available=false`. Die realen Storage-Felder, deren Leistungsrichtung und
das reale Ohmpilot-Leistungsfeld sind noch nicht bestätigt. Sie werden deshalb
nicht geraten; ihre spätere Abbildung ist in getrennten Adapterfunktionen
gekapselt.

Smart-Meter-Daten sind explizit über `FroniusClient.get_meter_realtime_data()`
abrufbar. Weil sie für das aktuelle Modell nicht benötigt werden, verursacht
ein normaler Live-Snapshot keinen zusätzlichen Meter-Request.

### Manuellen Snapshot abrufen

Die Base URL muss bis einschließlich `/solar_api/v1/` angegeben werden. Es
werden weder Geräteadresse noch Zugangsdaten im Repository gespeichert.

```bash
FRONIUS_BASE_URL=http://fronius-host.example/solar_api/v1/ python3 -m fronius
```

Alternativ sind Argumente möglich:

```bash
python3 -m fronius \
  --base-url http://fronius-host.example/solar_api/v1/ \
  --timeout 5
```

Die Ausgabe ist genau ein JSON-Objekt im internen `LiveData`-Format. Fehler
werden auf stderr gemeldet und führen zu einem Exit-Code ungleich null.

### Tests

Die Tests benötigen keine externe Anlage und verwenden anonymisierte lokale
Fixtures sowie einen ausschließlich lokalen HTTP-Testserver:

```bash
python3 -m unittest discover -v
```

## Collector und SQLite

Die nächste, weiterhin renderer-unabhängige Stufe ist:

```text
FroniusClient -> Adapter/LiveData -> Collector -> SQLite -> 5-Minuten-Daten
```

Der Collector fragt standardmäßig alle 10 Sekunden ab. `SOLAR_POLL_INTERVAL_SECONDS`
und `--interval` ändern das Intervall. Fehlerhafte Requests oder ungültige
Pflichtwerte werden geloggt, nicht als Nullmessung gespeichert, und der nächste
Zyklus wird trotzdem ausgeführt. Der Loop vermeidet durch monotone Zielzeiten
eine schleichende Intervallverschiebung und reagiert auf `SIGTERM`/`SIGINT`.

SQLite liegt standardmäßig unter `data/solar.db`; `SOLAR_DB_PATH` oder
`--db-path` wählen ein persistentes Docker-/NAS-Verzeichnis. `raw_samples`
enthält die normalisierten Einzelmessungen einschließlich des kumulierten
`energy_total_kwh`-Zählers. `aggregates_5m` enthält idempotente,
nur nach Bucket-Abschluss erzeugte Fünf-Minuten-Werte: Leistung als Mittelwert,
SoC und Tagesenergie als letzten Wert sowie Sample-Anzahl und Verfügbarkeit.

Zeitstempel werden kanonisch in UTC gespeichert. Lokale Bucket- und
Kalendertagslogik verwendet `zoneinfo` mit `Europe/Zurich`, einschließlich DST.
Rohdaten werden nach standardmäßig sieben Tagen gelöscht
(`SOLAR_RAW_RETENTION_DAYS`/`--retention-days`), aber ausschließlich, wenn ihr
Bucket bereits aggregiert und vollständig vor dem Retention-Cutoff abgeschlossen
ist. Buckets werden dabei immer vollständig statt sampleweise gelöscht.
Unveränderte Buckets werden nicht erneut aggregiert; neue verspätete Samples
werden über einen geänderten `sample_count` erkannt. Aggregate werden nicht
automatisch gelöscht.

Da `DAY_ENERGY` auf der realen Anlage auch bei laufender Produktion `null` sein
kann, ist dieser API-Wert nicht die einzige Quelle für den Dashboard-Tagesertrag.
`energy_total_kwh` stammt bevorzugt aus `Site.E_Total`, ersatzweise aus der
Summe gültiger `Inverters.*.E_Total`-Werte. Der Tagesertrag wird aus letztem
Tageszähler minus letztem Zähler vor der lokalen Tagesgrenze berechnet. Fehlt
der vorherige Wert, kann der früheste Tageswert eine als unvollständig markierte
Baseline bilden. Bei fehlender Baseline oder einem Zählerrücksprung werden die
vorhandenen 5-Minuten-Mittelwerte der PV-Leistung über ihre tatsächliche Dauer
integriert; negative Tageserträge werden nie ausgegeben.

Manuelle Befehle (die globale DB-Option steht vor dem Unterbefehl):

```bash
FRONIUS_BASE_URL=http://fronius-host.example/solar_api/v1/ \
  SOLAR_DB_PATH=data/solar.db python3 -m collector once
python3 -m collector --db-path data/solar.db status
python3 -m collector --db-path data/solar.db aggregate
FRONIUS_BASE_URL=http://fronius-host.example/solar_api/v1/ python3 -m collector loop
```

Eine produktive Compose-Aktivierung ist absichtlich noch nicht enthalten, damit
ein Pull keinen neuen Dauerprozess startet. Für einen späteren Container müssen
`FRONIUS_BASE_URL` und `SOLAR_DB_PATH` gesetzt und das Elternverzeichnis der DB
als persistentes Volume eingebunden werden.

Noch offen sind reale Tests von `DAY_ENERGY` tagsüber, einem aktiven Ohmpilot
und der Batterie nach ihrer Installation. Bis dahin bleiben die bestätigten
Adapter-Fallbacks unverändert.
