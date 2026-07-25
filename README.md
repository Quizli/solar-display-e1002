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
`heat_available=false`. Für Ohmpilot ist `PowerReal_PAC_Sum` inzwischen als
Leistung in Watt bestätigt; gültige Werte aller Geräte werden summiert und als
`heat_power_kw` ausgewiesen. Diese Leistung ist bereits in `-P_Load` und damit
in `house_power_kw` enthalten: Sie wird weder davon abgezogen noch zusätzlich
zum Hausverbrauch addiert. Die realen Storage-Felder und deren Leistungsrichtung
sind noch nicht bestätigt und bleiben in einer getrennten Adapterfunktion
gekapselt.

Smart-Meter-Daten sind explizit über `FroniusClient.get_meter_realtime_data()`
abrufbar. Weil sie für das aktuelle Modell nicht benötigt werden, verursacht
ein normaler Live-Snapshot keinen zusätzlichen Meter-Request.

### Installation

Vor Tests oder manueller Ausführung werden die wenigen Laufzeitabhängigkeiten
installiert:

```bash
python3 -m pip install -r requirements.txt
```

Python 3.9 und neuer verwenden `zoneinfo` aus der Standardbibliothek. Unter
Python 3.8 wird automatisch `backports.zoneinfo` verwendet; `tzdata` stellt die
IANA-Zeitzonendaten auch in reduzierten Python-/Docker-Umgebungen bereit. Die
fachliche Zeitzone bleibt `Europe/Zurich`. Kanonische UTC-Speicherung und
DST-Logik sind auf beiden Python-Varianten identisch.

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

Der produktive Compose-Betrieb startet Collector, Publisher und Webserver als
getrennte Dienste. `FRONIUS_BASE_URL` wird lokal über `.env` gesetzt; der
Containerpfad der persistenten Datenbank ist fest `/data/solar.db`.

Für eine lokale Konfiguration kann die sichere Vorlage kopiert werden:

```bash
cp .env.example .env
```

`.env` bleibt ausschließlich lokal und wird durch `.gitignore` nicht committed.
Die Vorlage enthält keine Zugangsdaten. Echte API-Keys und andere Secrets gehören
niemals nach GitHub, in den Code, in PR-Beschreibungen oder in die Dokumentation.

Noch offen sind reale Tests von `DAY_ENERGY` tagsüber und der Batterie nach ihrer
Installation. Bis dahin bleiben die bestätigten Adapter-Fallbacks unverändert.

## Live-Dashboard-Publisher

Die produktive Datenstrecke ist jetzt vollständig entkoppelt:

```text
Fronius -> Collector -> SQLite -> Dashboard-Publisher -> publish/dashboard.svg -> Nginx -> E1002
```

Der Collector speichert weiterhin typischerweise alle zehn Sekunden. Das davon
unabhängige Display-Intervall beträgt standardmäßig fünf Minuten. Für Solar-,
Haus-, Ohmpilot-, Batterie- und Netzleistung verwendet der Publisher nur einen
abgeschlossenen neuesten 5-Minuten-Bucket, dessen Ende gegenüber dem letzten
Raw-Snapshot höchstens zehn Minuten alt ist. Ein zweiter, nach `sample_count`
gewichteter Bucket wird ausschließlich bei direkter zeitlicher Nachbarschaft
verwendet. Bei alten Buckets oder einer Datenlücke erfolgt der Fallback auf den
einzelnen aktuellen Bucket beziehungsweise den letzten gültigen Raw-Snapshot.
Der Hauswert enthält die Ohmpilot-Leistung
bereits. Der Tagesertrag kommt ausschließlich aus `SolarDatabase.daily_energy()`;
der Eigenverbrauch wird aus den Tagesaggregaten integriert.

Lokale Befehle verwenden standardmäßig `data/solar.db` und
`publish/dashboard.svg`:

```bash
python3 -m dashboard once
python3 -m dashboard status
python3 -m dashboard loop
```

`status` schreibt nur das abgeleitete JSON-Modell. `once` publiziert atomar;
bei fehlenden Daten oder einem Renderfehler bleibt das letzte gute SVG erhalten.
`SOLAR_DB_PATH`, `DASHBOARD_OUTPUT_PATH`, `DASHBOARD_REFRESH_SECONDS` und
`DASHBOARD_STALE_SECONDS` überschreiben die Defaults.

Für Docker wird `SOLAR_DB_PATH` im Compose-File bewusst auf `/data/solar.db`
gesetzt, während `.env.example` den lokalen Pfad dokumentiert. `./data` und
`./publish` sind persistente Bind-Mounts. Nach dem Kopieren und Ausfüllen der
sicheren `.env`-Vorlage startet der Dauerbetrieb mit:

```bash
docker compose up -d --build
```

Das Dashboard ist anschließend unter
`http://<NAS-IP>:8088/dashboard.svg` erreichbar. Diagnose:

```bash
docker compose logs -f collector dashboard-publisher
docker compose exec dashboard-publisher python3 -m dashboard status
```

Wetter, Vorhersage, Sonnenzeiten, Charts, CO₂-Berechnung, wechselnde Facts und
eine direkte E1002-Upload-API sind bewusst nicht Teil dieser Etappe. Bis dahin
zeigt das eingefrorene Layout neutrale Striche und einen festen Live-Hinweis.

### Docker-Build-Kontext und Dateirechte

`.gitignore` verhindert, dass lokale Konfiguration und Laufzeitdaten versehentlich
in Git aufgenommen werden. Zusätzlich schützt `.dockerignore` den Build-Kontext
bei `COPY . .`: Insbesondere `.env`, SQLite-Dateien, `data/`, `publish/` und
Renderer-Ausgaben werden nicht an den Docker-Daemon übertragen und nicht ins
Image kopiert. Die Anwendung benötigt keine `.env` im Image. Compose verwendet
die lokale Datei ausschließlich zur Variablensubstitution und gibt jedem Dienst
nur seine benötigten Variablen weiter; Wettervariablen werden keinem Container
übergeben. `.env` darf weder committed noch in ein Image kopiert werden.

Collector und Publisher laufen nicht als root. Vor dem ersten Start werden die
lokalen IDs ermittelt:

```bash
id -u
id -g
```

Die Ergebnisse werden lokal als `SOLAR_UID` und `SOLAR_GID` in `.env`
eingetragen. Die Host-Verzeichnisse `./data` und `./publish` müssen für diesen
Benutzer beziehungsweise diese Gruppe schreibbar sein. Es werden bewusst keine
realen NAS- oder Benutzer-IDs im Repository vorgegeben.
