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
        if len(raw_name) < 4 or SKIP_HEADINGS.search(raw_name):
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

        # Length — first valid occurrence in a "Długość" line
        length_km = 0.0
        for line in lines:
            if re.search(r'D[łl]ugo[śs][ćc]', line, re.I):
                lm = parse_length_m(line)
                if lm > 0:
                    length_km = round(lm / 1000, 3)
                    break

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

    data           = load_existing()
    existing_zones = data.get('zones', [])
    log.info(f"Aktualna liczba stref: {len(existing_zones)}")

    all_scraped = []
    for road_type, roads in ROADS:
        for road in roads:
            all_scraped.extend(scrape_road(road_type, road))

    log.info(f"Zescrapowano łącznie {len(all_scraped)} stref")

    if not all_scraped:
        log.error("Nie udało się pobrać żadnych stref — przerywam")
        return

    merged, changes = merge_zones(existing_zones, all_scraped)

    if changes == 0:
        log.info("Brak zmian — zones.json jest aktualny")
    else:
        log.info(f"Wykryto {changes} zmian — zapisuję zones.json")
        data['zones']   = merged
        data['source']  = 'CANARD/GITD via naodcinku.pl (auto-scrape)'
        data['version'] = '2.1'
        save(data)

    log.info("═══ Koniec ═══")


if __name__ == '__main__':
    main()
