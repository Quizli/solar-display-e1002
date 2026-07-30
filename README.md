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
python3 -m dashboard health
python3 -m dashboard health --text
python3 -m dashboard loop
```

`status` schreibt nur das abgeleitete JSON-Modell. `health` prüft Datenbank,
Datenfrische, Modell und publiziertes SVG ohne Netzwerkzugriff oder Schreibzugriffe;
kompaktes JSON ist der Default, `--text` die lesbare Betriebsansicht. Die Exit-Codes
sind 0 (healthy), 1 (degraded) und 2 (unhealthy). `once` publiziert atomar;
bei fehlenden Daten oder einem Renderfehler bleibt das letzte gute SVG erhalten.
`SOLAR_DB_PATH`, `DASHBOARD_OUTPUT_PATH`, `DASHBOARD_REFRESH_SECONDS` und
`DASHBOARD_STALE_SECONDS` überschreiben die Defaults.

### Sonnenprognose und CO₂-Ersparnis

Sonnenaufgang, Sonnenuntergang und die prognostizierten Sonnenstunden für den
heutigen lokalen Tag stammen aus der Open-Meteo Forecast API. Open-Meteos
`sunshine_duration` ist die prognostizierte Sonnenscheindauer in Sekunden; das
Dashboard rechnet sie in Stunden um. Es handelt sich ausdrücklich um eine
Tagesprognose, nicht um historische Messdaten oder eine aus Bewölkung
abgeleitete Näherung. Der tägliche WMO-`weather_code` steuert das kompakte
Wettericon neben den Sonnenstunden. Bei einem fehlenden oder ungültigen Code
bleibt die Sonnenstundenanzeige mit einem neutralen Sonnenicon verfügbar.

Für den produktiven Abruf müssen `SOLAR_LAT` und `SOLAR_LON` in der lokalen
`.env` gesetzt werden. Die Vorlage enthält absichtlich keine privaten
Koordinaten. Standardmäßig wird Open-Meteo höchstens alle 1.800 Sekunden
abgerufen und die letzte gültige Antwort atomar gespeichert. Der lokale
CLI-Default ist `data/sun-data.json`; Compose setzt für den Publisher explizit
`SUN_DATA_CACHE_PATH=/data/sun-data.json`. Damit liegt der Docker-Cache im
bestehenden persistenten Mount `./data:/data` und benötigt kein weiteres
Volume. Bei einem
API-Ausfall bleibt ein Cache des heutigen Zürcher Kalendertags bis zu 21.600
Sekunden nutzbar. Danach erscheinen für alle drei Sonnenwerte neutrale Striche;
die Veröffentlichung der vorhandenen Solar-KPIs läuft trotzdem weiter.
`SUN_DATA_REFRESH_SECONDS` und `SUN_DATA_MAX_AGE_SECONDS` konfigurieren diese
Intervalle.

Die tägliche CO₂-Ersparnis wird aus dem realen Tagesertrag und standardmäßig
`CO2_AVOIDED_KG_PER_KWH=0.128` berechnet. Die 128 g CO₂eq/kWh dienen als
Vergleich zum durchschnittlichen Schweizer Verbraucher-Strommix. Der Wert ist
eine Schätzung der vermiedenen Klimawirkung und keine vollständige
Lebenszyklusanalyse der PV-Anlage. Faktor, Berechnung und Vergleichsbasis sind
auch in der JSON-Statusausgabe dokumentiert; ungültige Faktoren werden als
Konfigurationsfehler abgelehnt.

Für Docker wird `SOLAR_DB_PATH` im Compose-File bewusst auf `/data/solar.db`
gesetzt, während `.env.example` den lokalen Pfad dokumentiert. `./data` und
`./publish` sind persistente Bind-Mounts. Nach dem Kopieren und Ausfüllen der
sicheren `.env`-Vorlage startet der Dauerbetrieb mit:

```bash
docker compose up -d --build
```

Das Dashboard ist anschließend unter
`http://<NAS-IP>:8088/dashboard.svg` erreichbar. Der produktive Ablauf für
Deployment, Backup, Restore und Fehlersuche steht im [Operations-Handbuch](docs/OPERATIONS.md).
Diagnose:

```bash
sudo docker compose logs --tail=50 dashboard-publisher
sudo docker compose exec dashboard-publisher python3 -m dashboard status
```

### Öffentlicher JSON-Datenvertrag

Der bestehende Publisher erzeugt zusätzlich atomar `publish/dashboard.json`.
Diese Datei ist die einzige Datenquelle für das spätere Mobile-Dashboard; Browser
greifen weder auf Fronius noch auf SQLite zu. Schema-Version **1.0** hat folgende
öffentliche Top-Level-Struktur:

```text
schema_version, generated_at, data, status, header, live, today,
chart, insight, historical_comparison
```

`data` enthält Datenzeitpunkt, letzten Messzeitpunkt und Alter in Sekunden.
`status.overall` ist `fresh`, `degraded`, `stale` oder `missing`; der
Komponentenstatus `offline` kennzeichnet dabei eine ausgefallene Solardatenquelle.
`affected_components` nennt nur frontendtaugliche Bereiche, keine internen Fehler.
Optionale nicht verfügbare Werte sind JSON-`null`, Bereiche tragen zusätzlich
`available` oder `missing`. Nicht endliche Zahlen werden nie publiziert.

`header` enthält den aktuellen lokalen ISO-Zeitpunkt und Tag in `Europe/Zurich`
und bleibt auch bei fehlenden oder veralteten Messdaten verfügbar. Der davon
getrennte Messzeitpunkt steht unter `data`. Zusätzlich enthält `header` Wetterzustand,
Sonnenstunden sowie Sonnenauf- und -untergang. `live` und `today` verwenden kW,
kWh, kg und Prozent. Die Werte unter `live` stammen aus dem neuesten Raw-Snapshot;
der Hauswert ist wie im eInk-Dashboard der Verbrauch ohne
den separat ausgewiesenen Ohmpilot-Wert. Batterie- und Netzfluss enthalten sowohl
den vorzeichenbehafteten Wert (`power_kw`) als auch `magnitude_kw` und eine
explizite Richtung. Batterie: positiv/`charging`, negativ/`discharging`;
Netz: positiv/`exporting`, negativ/`importing`; ausserdem sind `idle` und
`unavailable` möglich. Damit muss ein Frontend keine Vorzeichen interpretieren.
Die bestehende `battery_available`-Übergangslogik bleibt massgeblich.

`chart.series` enthält ausschliesslich abgeschlossene 5-Minuten-Buckets mit
UTC-Start/-Ende, tatsächlichen Mittelwerten für Solar, gesamten Hausverbrauch
und Batteriefluss, dem letzten Batteriestand des Buckets sowie Sample-Anzahl.
Nicht verfügbare Batteriestände werden als `null` publiziert. Es werden keine
SVG-Koordinaten exportiert. `insight` enthält denselben stündlich persistierten
Fact wie das eInk-Dashboard. `historical_comparison` wird unabhängig davon aus
der bestehenden History-Logik ermittelt und ist entweder `null` oder trennt Typ,
Aussage, Bezugszeitraum, Vergleichs- und Basiswert, prozentuale Differenz und
Richtung (`higher`, `lower`, `similar`). Vergleichstag und Anzahl Basistage
bleiben, soweit vorhanden, als eigene Felder erhalten. Standardmässig vergleicht
die Kachel den jüngsten gemäss 95-%-Regel vollständigen Zürcher Kalendertag mit
dem Mittel der zwei bis sieben jüngsten vollständigen Tage davor. Der
Vergleichstag ist nie Teil dieses Mittels; Lücken werden übersprungen. Ein
gleichzeitig aktiver Rekord-Fact bleibt als `insight` sichtbar, während die
Kachel diesen nicht wiederholt. Fehlen zwei qualifizierte Referenztage oder ist
deren Mittel null, bleibt `historical_comparison` kontrolliert `null`.
Ein Durchschnittsvergleich über denselben Tag wie ein Rekord ist nicht
redundant: Er setzt den Tagesertrag ausschliesslich zum Mittelwert ins
Verhältnis und behauptet weder einen Rekord noch eine Überschreitung des alten
Rekords. Rekordkandidaten werden bei aktivem Rekord-Insight semantisch anhand
ihres Typs unterdrückt; blosse Textungleichheit gilt nicht als
Redundanznachweis. Da `HIST_AVERAGE` konstruktiv keine Rekordaussage erzeugen
kann, ist kein Ausweichen auf einen älteren Vergleichstag erforderlich.

Der JSON-Takt beträgt standardmässig 15 Sekunden
(`DASHBOARD_JSON_REFRESH_SECONDS`), passend zum 10-Sekunden-Collector. Der
unveränderte SVG-Takt bleibt 300 Sekunden. Beide Dateien entstehen im vorhandenen
Publisher-Service mittels `fsync` und atomarem Replace. Das JSON basiert auf
einer expliziten Allowlist und enthält insbesondere keine Endpunkte,
Koordinaten, Zugangsdaten, Gerätekennungen, internen Pfade, Konfigurationswerte
oder rohen Exceptions. Pfad und Takt können lokal mit
`DASHBOARD_JSON_OUTPUT_PATH` beziehungsweise
`DASHBOARD_JSON_REFRESH_SECONDS` gesetzt werden.

Allgemeine Wetterdaten, Temperatur, Niederschlagsmengen, historische
Sonnenstunden und eine direkte E1002-Upload-API sind nicht Bestandteil.
Fact-Auswahlen werden dagegen pro lokaler Stunde dauerhaft gespeichert; zusätzlich
können nach mehreren abgeschlossenen Tagen historische Ertragsvergleiche erscheinen.

### Web-Dashboard-Oberfläche

Die statische Produktionsoberfläche liegt in `web/dashboard.html`; ihr
Solar-Glass-Theme und das lokale SVG-Sprite liegen unter `web/assets/`. Compose
bindet `web/` und `publish/` als getrennte schreibgeschützte Verzeichnisse in
denselben Nginx-Service ein. Explizite Nginx-Routen liefern die Oberfläche und
ihre Assets aus `web/`, während `dashboard.json` und `dashboard.svg` weiterhin
direkt aus `publish/` stammen. Die getrennten Mount-Ziele vermeiden
verschachtelte Bind-Mounts und funktionieren damit auch mit der
Synology-Docker-Runtime. Eine zweite Webanwendung oder ein zusätzlicher Service
ist nicht nötig.

Die produktive Datenbindung liegt ohne Build-Schritt in
`web/assets/dashboard.js` und wird lokal mit `defer` geladen. Sie liest als
einzige Datenquelle die relative Datei `dashboard.json` mit `cache: no-store`,
prüft Schema-Version **1.0**, ruft sofort und danach alle 15 Sekunden ab und
verhindert überlappende Abrufe. Ein Abruf wird nach 10 Sekunden abgebrochen;
beim erneuten Sichtbarwerden des Tabs erfolgt ein sofortiger Folgeabruf.

Die semantischen Komponenten werden über `data-component`, Vertragsfelder über
`data-field` und gerichtete Energieflüsse ausschliesslich über die publizierten
`direction`-Felder gebunden. `data-state` unterstützt `loading`, `fresh`,
`degraded`, `stale`, `missing` und `offline`. Komponentenfehler bleiben lokal;
Payloads werden vor der Darstellung vollständig validiert; dabei wird auch der
reale Wetterstatus `invalid` als komponentenbezogener Degraded-Fall unterstützt.
Erst ein fertiges View-Model wird atomar ins DOM übernommen. Strukturell oder typmässig ungültige
Payloads werden ohne sichtbare Teilaktualisierung verworfen. Bei Netzwerk-,
HTTP-, JSON-, Schema- oder Typfehlern bleiben nach dem ersten Erfolg die letzten
vollständig validierten Werte sichtbar; der Offline-Stand verwendet `generated_at`. Eine
erfolgreiche Antwort stellt den aktuellen Zustand automatisch wieder her. Fresh
zeigt das gerundete `data.age_seconds`. Zahlen und Zeiten werden in Schweizer
Darstellung beziehungsweise stets in `Europe/Zurich` formatiert.
Die 20 Solarsegmente verwenden die zentrale UI-Anlagenkonstante **21.78 kWp**,
die Batteriesegmente den Bereich 0–100 Prozent.

Der responsive Tagesverlauf wird direkt als lokales SVG erzeugt: Solar gelb mit
Fläche, Hausverbrauch blau und Batteriestand grün. Solar und Haus verwenden die
dynamische linke kW-Achse, der Batteriestand die feste rechte Achse von 0 bis
100 Prozent. Die Zeitachse reicht von 00:00 bis 24:00; Datenlücken und
`null`-Werte werden nicht interpoliert. Eine proportionale ViewBox hält
Achsentexte auch auf mobilen Viewports unverzerrt; beide Plotränder werden aus
den Achsenbeschriftungen berechnet, damit kein Wert abgeschnitten wird. Es gibt keine Frameworks, externen Schriftdateien,
Chartbibliotheken, CDNs, Tracker oder sonstigen Drittkomponenten zur Laufzeit.

Das Layout ist mobile-first. Die Energieflüsse bleiben als eine Kachel mit
vier Segmenten im 2×2-Raster gruppiert. Ab 700 px stehen Hero-Karten sowie
Tagesverlauf und Insight nebeneinander. Ab 1050 px wird der Tagesverlauf zur
grossen Desktop-Hauptfläche, während Bilanz und Insight den Seitenbereich
bilden. Safe-Area-Inset, ein 320-px-Fallback und eine undurchsichtige
`backdrop-filter`-Fallbackfläche sind enthalten. Die HTML-Symbole sind als lokale Inline-SVG-Familie ohne externe Abhängigkeit eingebettet.

### Docker-Build-Kontext und Dateirechte

`.gitignore` verhindert, dass lokale Konfiguration und Laufzeitdaten versehentlich
in Git aufgenommen werden. Zusätzlich schützt `.dockerignore` den Build-Kontext
bei `COPY . .`: Insbesondere `.env`, SQLite-Dateien, `data/`, `publish/` und
Renderer-Ausgaben werden nicht an den Docker-Daemon übertragen und nicht ins
Image kopiert. Die Anwendung benötigt keine `.env` im Image. Compose verwendet
die lokale Datei ausschließlich zur Variablensubstitution und gibt jedem Dienst
nur seine benötigten Variablen weiter; Wettervariablen werden keinem Container
übergeben. `.env` darf weder committed noch in ein Image kopiert werden.

Collector und Publisher laufen als `SOLAR_UID:SOLAR_GID` und nicht als root.
Vor dem ersten Start werden die lokalen IDs ermittelt:

```bash
id -u
id -g
```

Die Ergebnisse werden lokal als `SOLAR_UID` und `SOLAR_GID` in `.env`
eingetragen. Die Host-Verzeichnisse `./data` und `./publish` müssen für diesen
Benutzer beziehungsweise diese Gruppe schreibbar sein. Es werden bewusst keine
realen NAS- oder Benutzer-IDs im Repository vorgegeben. Das atomisch
publizierte `dashboard.svg` erhält bewusst den Modus `0644`, damit der separate
Nginx-Service die Datei lesen kann. Das Verzeichnis `publish/` muss für Nginx
außerdem mindestens durchsuchbar sein; globale Schreibrechte wie `0666` oder
`0777` sind weder notwendig noch vorgesehen.

## Rekorde, Facts und Anzeige-Skalierung

Ein Tagesrekord ist der höchste gültige Ertrag unter den vollständig abgeschlossenen,
aggregatebasierten Aufzeichnungstagen. Ein laufender Tag benötigt mindestens zwei
frühere abgeschlossene Tage und muss den bisherigen Höchstwert um mehr als 0,1 kWh
übertreffen. Die Aussage lautet bewusst «seit Beginn der Aufzeichnung», nicht
«Anlagenrekord». Nach der Überschreitung hat der Rekord am laufenden lokalen Tag
Priorität und wird mit dem steigenden Tageswert aktualisiert; nach Mitternacht bleibt
der endgültige Rekord des Vortags noch den ganzen Folgetag angeheftet. Fehlende oder
veraltete Betriebsdaten bleiben höher priorisiert. Danach folgen andere historische
Vergleiche, Katalog-Facts und technische Rückfälle.

Für Rekordvergleiche gilt ein vergangener Tag nur dann als vollständig, wenn seine
5-Minuten-Aggregate den realen UTC-Zeitraum zwischen beiden Zürcher Mitternachten
mit höchstens zehn Minuten Rand- oder Binnenlücke abdecken und mindestens 95 Prozent
der für diese reale Tagesdauer erwarteten eindeutigen, gültigen Buckets enthalten.
Die Mindestzahl wird mit `ceil(expected × 0.95)` aufgerundet: 263 von 276 Buckets am
23-Stunden-Tag, 274 von 288 am normalen Tag und 285 von 300 am 25-Stunden-Tag.
Dadurch werden einzelne, halb abgedeckte und abgebrochene Tage ausgeschlossen;
Duplikate erhöhen die Quote nicht. Die Zürcher DST-Wechseltage werden ohne naive
24-Stunden-Annahme über ihre tatsächlichen UTC-Grenzen geprüft.
Die Aggregate aller historischen Tage werden dazu in einer gemeinsamen SQL-Abfrage
geladen, lokal gruppiert und in einem Durchlauf qualifiziert. Die so ermittelten
historischen Kandidaten werden innerhalb derselben Publisher-Auswertung für Insight
und historischen Vergleich wiederverwendet; es gibt keine Abfrage pro Historientag.
Gewöhnliche Produktionszustände wie Nacht, `ZERO` oder ein noch nicht angelaufener
Tag verdrängen einen aktiven Rekord nicht.

Reguläre Katalog-Facts rotieren unabhängig davon. Die Auswahl vermeidet zuerst heute
verwendete Facts, die letzten zwölf tatsächlich angezeigten Katalog-Facts und die
vorherige Familie; gestern gezeigte Motive werden innerhalb derselben Stufe
zurückgestellt. Ist der Pool erschöpft, erscheint der am längsten nicht gezeigte
geeignete Fact statt eines technischen Textes. Status- und historische Meldungen
zählen nicht zum Zwölfer-Gedächtnis.

`SOLAR_PROGRESS_MAX_KW = 18.0` kalibriert ausschliesslich die 20 Segmente der
Solar-Progressbars in HTML und eInk. Die installierte Leistung bleibt 21,78 kWp;
Messwerte werden nicht begrenzt. Die feste eInk-Chartskala bleibt 24 kW und das
HTML-Chart bleibt dynamisch.

Die HTML-Oberfläche verwendet eine eigene, abgerundete Inline-SVG-Icon-Familie mit
konsistenter Strichstärke und zurückhaltenden Farbflächen. Sie benötigt weder CDN,
Icon-Font noch externe SVG- oder Rasterressourcen. Die reduzierten eInk-Icons und die
gefreezte 800×480-Geometrie bleiben davon getrennt und unverändert. Verbleibende
Lucide-basierte beziehungsweise davon abgeleitete Pfade sind trotz Inline-Einbettung
weiterhin in `web/THIRD_PARTY_NOTICES.md` unter der ISC-Lizenz ausgewiesen.
