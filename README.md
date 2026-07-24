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
`DAY_ENERGY` wird von Wh nach kWh umgerechnet. Fehlt der Tageswert oder ist er
`null`, wird vorläufig `0.0` verwendet. Die Pflichtwerte `P_PV`, `P_Load` und
`P_Grid` sowie der Zeitstempel werden dagegen validiert: fehlende, ungültige
oder nicht endliche Werte brechen die Normalisierung ab, damit ein API-Fehler
nicht als echter Null-Snapshot erscheint.

Storage und Ohmpilot sind optional. Fehlende Antworten, `null` oder leere
`Data`-Objekte ergeben sichere Nullwerte mit `battery_available=false` bzw.
`heat_available=false`. Die realen Storage-Felder, deren Leistungsrichtung und
das reale Ohmpilot-Leistungsfeld sind noch nicht bestätigt. Sie werden deshalb
nicht geraten; ihre spätere Abbildung ist in getrennten Adapterfunktionen
gekapselt.

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
