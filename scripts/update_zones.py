#!/usr/bin/env python3
"""
OPP Zones Auto-Updater
Scrape naodcinku.pl (source: CANARD/GITD),
porównaj z aktualnym zones.json, zapisz zaktualizowaną wersję.
"""

import json
import re
import time
import logging
import unicodedata
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger(__name__)

ZONES_FILE = Path(__file__).parent.parent / 'zones.json'
BASE_URL   = 'https://naodcinku.pl'
HEADERS    = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
    'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'pl-PL,pl;q=0.9,en;q=0.7',
}
SLEEP_BETWEEN = 1.5

# Overpass API — fotoradary w Polsce
OVERPASS_URL       = 'https://overpass-api.de/api/interpreter'
OVERPASS_HEADERS   = {'User-Agent': 'OPP-Monitor/1.0 (github.com/ciupas/opp-zones; pciupinski@gmail.com)'}
# Bounding box Polski: south, west, north, east
POLAND_BBOX        = (49.0, 14.1, 54.9, 24.1)

ROADS = [
    ('autostrada',       ['a1', 'a2', 'a4', 'a8']),
    ('droga-ekspresowa', ['s2', 's3', 's6', 's7', 's8', 's11', 's14', 's17', 's51', 's52']),
]

SKIP_HEADINGS = re.compile(
    r'odcinkowy\s*pomiar|autostrady|ekspresowe|menu|nawigacja|mapa|kontakt|strona\s*g',
    re.I
)


# ── Helpers ───────────────────────────────────────────────────

def load_existing() -> dict:
    if ZONES_FILE.exists():
        with open(ZONES_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {"version": "2.1", "zones": [], "cameras": []}


def save(data: dict):
    data['updated']     = str(date.today())
    data['total_zones'] = len(data['zones'])
    with open(ZONES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"Zapisano {len(data['zones'])} stref do {ZONES_FILE}")


def get_soup(url: str) -> BeautifulSoup | None:
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        log.warning(f"Błąd pobierania {url}: {e}")
        return None


def slugify(text: str) -> str:
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore').decode()
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'[\s-]+', '_', text).strip('_')[:40]


def parse_coord_pair(text: str) -> tuple[float, float] | None:
    m = re.search(r'(\d{2}\.\d{4,})[,\s]+(\d{2}\.\d{4,})', text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


def parse_length_m(text: str) -> float:
    """
    Polish number formatting:
      '14,061 m'  → 14061  (comma = thousands separator)
      '4 041 m'   → 4041   (space = thousands separator)
      '7.422 km'  → 7422
    """
    m = re.search(r'([\d\.]+)\s*km\b', text, re.I)
    if m:
        try:
            return float(m.group(1)) * 1000
        except ValueError:
            pass
    m = re.search(r'(\d(?:[\d ,]*\d)?)\s*m\b', text)
    if m:
        s = m.group(1).replace(' ', '').replace(',', '')
        try:
            v = float(s)
            if v > 100:
                return v
        except ValueError:
            pass
    return 0.0


# ── Scraper ───────────────────────────────────────────────────

def scrape_road(road_type: str, road: str) -> list[dict]:
    url = f"{BASE_URL}/{road_type}/{road}/"
    time.sleep(SLEEP_BETWEEN)
    log.info(f"Pobieranie {url}")
    soup = get_soup(url)
    if not soup:
        return []

    zones: list[dict] = []
    seen_names: set[str] = set()

    for heading in soup.find_all(['h2', 'h3']):
        raw_name = heading.get_text(strip=True)
        # Only real zone headings start with "Odcinek" (includes "Odcinek Tunel …")
        if not raw_name.lower().startswith('odcinek'):
            continue

        # Deduplicate: ignore direction suffix in parentheses
        base_name = raw_name.split('(')[0].strip()
        if base_name in seen_names:
            continue
        seen_names.add(base_name)

        # Collect sibling text until the next heading
        text_blocks = []
        for sibling in heading.next_siblings:
            if getattr(sibling, 'name', None) in ['h2', 'h3']:
                break
            if hasattr(sibling, 'get_text'):
                text_blocks.append(sibling.get_text('\n', strip=True))
        text = '\n'.join(text_blocks)

        if not text or len(text) < 20:
            continue

        lines = text.split('\n')

        # Voivodeship
        voivodeship = ''
        m = re.search(r'Wojew[oó]dztwo[:\s]+([^\n]+)', text, re.I)
        if m:
            voivodeship = m.group(1).strip().lower()

        # Location
        location = ''
        m = re.search(r'Lokalizacja[:\s]+([^\n]+)', text, re.I)
        if m:
            location = m.group(1).strip()

        # Speed limits
        limit = 120
        m = re.search(r'osobowe[^\d]+(\d+)\s*km/h', text, re.I)
        if m:
            limit = int(m.group(1))
        elif (m := re.search(r'(\d+)\s*km/h', text)):
            limit = int(m.group(1))

        limit_trucks = 80
        m = re.search(r'ci[eę][żz]arowe[^\d]+(\d+)\s*km/h', text, re.I)
        if m:
            limit_trucks = int(m.group(1))

        # Length — find first number+m following any "Długość" label (multi-line safe)
        length_km = 0.0
        m = re.search(r'D[łl]ugo[śs][ćc].*?(\d[\d ,]*\d|\d)\s*m\b', text, re.I | re.DOTALL)
        if m:
            lm = parse_length_m(m.group(1) + ' m')
            if lm > 0:
                length_km = round(lm / 1000, 3)

        # Coordinates — prefer labelled "Start:" / "Koniec:" lines
        start_lat = start_lon = end_lat = end_lon = 0.0
        for line in lines:
            if re.search(r'^Start[:\s]', line, re.I) and start_lat == 0.0:
                c = parse_coord_pair(line)
                if c:
                    start_lat, start_lon = c
            elif re.search(r'^Koniec[:\s]', line, re.I) and end_lat == 0.0:
                c = parse_coord_pair(line)
                if c:
                    end_lat, end_lon = c

        # Fallback: first and last coord pair anywhere in the block
        if start_lat == 0.0:
            all_coords = re.findall(r'(\d{2}\.\d{4,})[,\s]+(\d{2}\.\d{4,})', text)
            if len(all_coords) >= 2:
                start_lat, start_lon = float(all_coords[0][0]),  float(all_coords[0][1])
                end_lat,   end_lon   = float(all_coords[-1][0]), float(all_coords[-1][1])
            elif len(all_coords) == 1:
                start_lat, start_lon = float(all_coords[0][0]), float(all_coords[0][1])

        zone = {
            "id":           f"OPP_{road.upper()}_{slugify(base_name)}",
            "name":         base_name,
            "road":         road.upper(),
            "voivodeship":  voivodeship,
            "location":     location,
            "limit":        limit,
            "limit_trucks": limit_trucks,
            "start":        {"lat": start_lat, "lon": start_lon},
            "end":          {"lat": end_lat,   "lon": end_lon},
            "length_km":    length_km,
            "direction":    "both",
            "_source_url":  url,
        }
        zones.append(zone)
        log.info(f"  ✓ {base_name[:50]} ({road.upper()}, {limit} km/h, {length_km} km)")

    log.info(f"  → {len(zones)} stref na {road.upper()}")
    return zones


# ── Cameras (OpenStreetMap / Overpass) ───────────────────────

def _overpass_query(query: str) -> list[dict]:
    """Wykonaj zapytanie Overpass, zwróć listę elementów."""
    try:
        r = requests.get(OVERPASS_URL, params={'data': query},
                         headers=OVERPASS_HEADERS, timeout=120)
        r.raise_for_status()
        return r.json().get('elements', [])
    except Exception as exc:
        log.warning(f"Błąd Overpass API: {exc}")
        return []


def scrape_cameras_osm() -> list[dict]:
    """Pobierz fotoradary z OpenStreetMap przez Overpass API.

    Polska jest dzielona na 4 ćwiartki żeby uniknąć timeoutu Overpass.
    """
    log.info("Pobieranie fotoradarów z OpenStreetMap (Overpass API)…")

    # Podział Polski na 4 regiony: (south, west, north, east)
    regions = [
        (52.0, 14.1, 54.9, 19.0),   # NW
        (52.0, 19.0, 54.9, 24.1),   # NE
        (49.0, 14.1, 52.0, 19.0),   # SW
        (49.0, 19.0, 52.0, 24.1),   # SE
    ]

    all_elements: list[dict] = []
    for i, (s, w, n, e) in enumerate(regions, 1):
        query = (
            f'[out:json][timeout:60];'
            f'node["highway"="speed_camera"]({s},{w},{n},{e});'
            f'out body;'
        )
        log.info(f"  Region {i}/4 ({s},{w} → {n},{e})…")
        elements = _overpass_query(query)
        log.info(f"    {len(elements)} węzłów")
        all_elements.extend(elements)
        if i < len(regions):
            time.sleep(2)

    log.info(f"Łącznie pobrano {len(all_elements)} fotoradarów z OSM")

    cameras = []
    seen_ids: set[int] = set()
    for el in all_elements:
        if el.get('type') != 'node':
            continue
        osm_id = el['id']
        if osm_id in seen_ids:
            continue
        seen_ids.add(osm_id)
        tags = el.get('tags', {})

        limit = 0
        raw = tags.get('maxspeed', '')
        m = re.search(r'(\d+)', raw)
        if m:
            limit = int(m.group(1))

        direction = tags.get('direction', '')
        try:
            direction = int(direction)
        except (ValueError, TypeError):
            direction = None

        cameras.append({
            "id":          f"CAM_{osm_id}",
            "lat":         el['lat'],
            "lon":         el['lon'],
            "limit":       limit,
            "direction":   direction,
            "enforcement": tags.get('enforcement', 'maxspeed'),
            "ref":         tags.get('ref', ''),
            "operator":    tags.get('operator', ''),
            "_osm_id":     osm_id,
            "_source":     "OpenStreetMap",
        })

    log.info(f"Pobrano {len(cameras)} fotoradarów z OSM")
    return cameras


def merge_cameras(existing: list[dict], scraped: list[dict]) -> tuple[list[dict], int]:
    existing_by_id = {c['id']: c for c in existing}
    changes = 0
    merged  = list(existing)

    for sc in scraped:
        sid = sc['id']
        if sid not in existing_by_id:
            merged.append(sc)
            changes += 1
        else:
            ec      = existing_by_id[sid]
            updated = False
            if sc['limit'] > 0 and sc['limit'] != ec.get('limit'):
                ec['limit'] = sc['limit']
                updated = True
            if sc['direction'] is not None and sc['direction'] != ec.get('direction'):
                ec['direction'] = sc['direction']
                updated = True
            if updated:
                changes += 1

    return merged, changes


# ── Merging ───────────────────────────────────────────────────

def merge_zones(existing: list[dict], scraped: list[dict]) -> tuple[list[dict], int]:
    existing_by_id = {z['id']: z for z in existing}
    changes = 0
    merged  = list(existing)

    for sz in scraped:
        sid = sz['id']
        if sid not in existing_by_id:
            log.info(f"  + NOWA strefa: {sz['name']}")
            merged.append(sz)
            changes += 1
        else:
            ez      = existing_by_id[sid]
            updated = False

            if sz['limit'] != ez.get('limit') and sz['limit'] > 0:
                log.info(f"  ~ Zmiana limitu {sid}: {ez.get('limit')} → {sz['limit']}")
                ez['limit'] = sz['limit']
                updated = True

            if sz['length_km'] > 0 and abs(sz['length_km'] - ez.get('length_km', 0)) > 0.05:
                log.info(f"  ~ Zmiana długości {sid}: {ez.get('length_km')} → {sz['length_km']}")
                ez['length_km'] = sz['length_km']
                updated = True

            if ez.get('start', {}).get('lat', 0) == 0 and sz['start']['lat'] != 0:
                ez['start'] = sz['start']
                ez['end']   = sz['end']
                updated = True

            if updated:
                changes += 1

    return merged, changes


# ── Main ──────────────────────────────────────────────────────

def main():
    log.info("═══ OPP Zones Updater ═══")
    log.info(f"Plik docelowy: {ZONES_FILE}")

    data            = load_existing()
    existing_zones  = data.get('zones', [])
    existing_cameras = data.get('cameras', [])
    log.info(f"Aktualne dane: {len(existing_zones)} stref, {len(existing_cameras)} fotoradarów")

    # ── OPP strefy ────────────────────────────────────────────
    all_scraped = []
    for road_type, roads in ROADS:
        for road in roads:
            all_scraped.extend(scrape_road(road_type, road))
    log.info(f"Zescrapowano łącznie {len(all_scraped)} stref OPP")

    if not all_scraped:
        log.error("Nie udało się pobrać żadnych stref — przerywam")
        return

    merged_zones, zone_changes = merge_zones(existing_zones, all_scraped)

    # ── Fotoradary OSM ────────────────────────────────────────
    scraped_cameras = scrape_cameras_osm()
    merged_cameras, cam_changes = merge_cameras(existing_cameras, scraped_cameras)

    # ── Zapis ─────────────────────────────────────────────────
    total_changes = zone_changes + cam_changes
    if total_changes == 0:
        log.info("Brak zmian — zones.json jest aktualny")
    else:
        log.info(f"Zmiany: {zone_changes} stref, {cam_changes} fotoradarów — zapisuję")
        data['zones']           = merged_zones
        data['cameras']         = merged_cameras
        data['total_cameras']   = len(merged_cameras)
        data['source']          = 'OPP: naodcinku.pl | Kamery: OpenStreetMap (Overpass)'
        data['version']         = '2.1'
        save(data)

    log.info("═══ Koniec ═══")


if __name__ == '__main__':
    main()
