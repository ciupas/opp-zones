# OPP Zones Poland

Automatycznie aktualizowana baza danych stref odcinkowego pomiaru prędkości (OPP) i fotoradarów w Polsce.

## Dane

Plik [`zones.json`](zones.json) zawiera dwa zbiory danych:

| Typ | Źródło | Ilość |
|---|---|---|
| Strefy OPP (odcinkowe) | [naodcinku.pl](https://naodcinku.pl) (CANARD/GITD) | ~43 |
| Fotoradary (punktowe) | [OpenStreetMap](https://www.openstreetmap.org) | ~2000 |

Dane są aktualizowane automatycznie **co poniedziałek o 6:00 UTC** przez GitHub Actions.

## Format danych

### Strefa OPP (`zones[]`)

```json
{
  "id": "OPP_A1_odcinek_a1_siemionki_lubien_kujawski",
  "name": "Odcinek A1 – Siemionki ↔ Lubień Kujawski",
  "road": "A1",
  "voivodeship": "kujawsko-pomorskie",
  "location": "Siemionki, gmina Lubień Kujawski, powiat włocławski",
  "limit": 140,
  "limit_trucks": 80,
  "start": { "lat": 52.440861, "lon": 19.236581 },
  "end":   { "lat": 52.339944, "lon": 19.356483 },
  "length_km": 14.025,
  "direction": "both"
}
```

| Pole | Opis |
|---|---|
| `id` | Unikalny identyfikator |
| `road` | Numer drogi (A1, S7, itp.) |
| `limit` | Limit prędkości dla samochodów osobowych (km/h) |
| `limit_trucks` | Limit dla samochodów ciężarowych (km/h) |
| `start` / `end` | Współrzędne GPS początku i końca strefy |
| `length_km` | Długość odcinka w km |
| `direction` | Kierunek pomiaru (`both` / `forward` / `backward`) |

### Fotoradar (`cameras[]`)

```json
{
  "id": "CAM_34014209",
  "lat": 53.3360475,
  "lon": 18.4689324,
  "limit": 50,
  "direction": 90,
  "enforcement": "maxspeed",
  "ref": "PLN.1.030",
  "operator": "CANARD",
  "_osm_id": 34014209,
  "_source": "OpenStreetMap"
}
```

| Pole | Opis |
|---|---|
| `lat` / `lon` | Współrzędne GPS kamery |
| `limit` | Mierzony limit prędkości (km/h), `0` = nieznany |
| `direction` | Kierunek mierzenia (stopnie, 0–360), `null` = nieznany |
| `enforcement` | Typ egzekucji (`maxspeed`, `traffic_signals`, itp.) |
| `ref` | Numer referencyjny urządzenia (CANARD) |
| `operator` | Operator (`CANARD`, `ITD`, itp.) |

## Pobieranie danych

Dane są dostępne jako surowy plik JSON bezpośrednio z GitHuba:

```
https://raw.githubusercontent.com/ciupas/opp-zones/main/zones.json
```

### Przykład (Python)

```python
import requests

data = requests.get(
    "https://raw.githubusercontent.com/ciupas/opp-zones/main/zones.json"
).json()

print(f"Strefy OPP: {len(data['zones'])}")
print(f"Fotoradary: {len(data['cameras'])}")
```

### Przykład (Arduino/ESP32)

```cpp
HTTPClient http;
http.begin("https://raw.githubusercontent.com/ciupas/opp-zones/main/zones.json");
int code = http.GET();
if (code == 200) {
    String payload = http.getString();
    // parsuj JSON...
}
```

## Aktualizacja ręczna

W zakładce **Actions** → **Aktualizacja bazy OPP** → **Run workflow**.

## Uruchomienie lokalne

```bash
pip install requests beautifulsoup4 lxml
python scripts/update_zones.py
```

## Źródła

- **Strefy OPP**: [naodcinku.pl](https://naodcinku.pl) — dane z CANARD (Centrum Automatycznego Nadzoru nad Ruchem Drogowym / GITD)
- **Fotoradary**: [OpenStreetMap](https://www.openstreetmap.org) via [Overpass API](https://overpass-api.de) — dane na licencji [ODbL](https://opendatacommons.org/licenses/odbl/)
