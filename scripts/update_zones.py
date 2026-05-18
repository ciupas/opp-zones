#!/usr/bin/env python3
"""
OPP Zones Auto-Updater
Scrape odcinkowy.pl i naodcinku.pl,
porównaj z aktualnym zones.json,
zapisz zaktualizowaną wersję.
"""

import json
import re
import time
import logging
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Konfiguracja ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
log = logging.getLogger(__name__)

ZONES_FILE   = Path(__file__).parent.parent / 'zones.json'
SOURCE_URL   = 'https://odcinkowy.pl/odcinki/'
HEADERS      = {
    'User-Agent': 'Mozilla/5.0 (compatible; OPP-Monitor-Bot/1.0; '
                  '+https://github.com/opp-monitor)'
}
SLEEP_BETWEEN = 1.5   # sekund między requestami — bądź grzeczny!

# ── Pomocnicze ────────────────────────────────────────────────

def load_existing() -> dict:
    """Wczytaj aktualny zones.json."""
    if ZONES_FILE.exists():
        with open(ZONES_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {"version": "1.0", "zones": [], "cameras": []}


def save(data: dict):
    """Zapisz zaktualizowany zones.json."""
    data['updated'] = str(date.today())
    data['total_zones'] = len(data['zones'])
    with open(ZONES_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    log.info(f"Zapisano {len(data['zones'])} stref do {ZONES_FILE}")


def get_soup(url: str) -> BeautifulSoup | None:
    """Pobierz stronę i zwróć BeautifulSoup."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return BeautifulSoup(r.text, 'lxml')
    except Exception as e:
        log.warning(f"Błąd pobierania {url}: {e}")
        return None


def parse_limit(text: str) -> int:
    """Wyciągnij limit prędkości z tekstu np. '120 km/h' → 120."""
    m = re.search(r'(\d+)\s*km/h', text, re.IGNORECASE)
    return int(m.group(1)) if m else 120


def parse_length(text: str) -> float:
    """Wyciągnij długość z tekstu np. '14,5 km' → 14.5."""
    m = re.search(r'([\d,\.]+)\s*km', text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(',', '.'))
    return 0.0


# ── Scraper odcinkowy.pl ──────────────────────────────────────

def scrape_zone_list() -> list[dict]:
    """
    Pobierz listę wszystkich stref z odcinkowy.pl/odcinki/
    Zwraca listę słowników z podstawowymi danymi i URL strony szczegółowej.
    """
    log.info(f"Pobieranie listy stref z {SOURCE_URL}")
    soup = get_soup(SOURCE_URL)
    if not soup:
        return []

    zones = []
    # Szukaj linków do stron poszczególnych odcinków
    for link in soup.select('a[href*="/odcinki/"]'):
        href = link.get('href', '')
        # Pomijamy stronę główną listy
        if href.endswith('/odcinki/') or href == SOURCE_URL:
            continue

        # Wyciągnij ID z URL np. /odcinki/33-wezel-minsk-mazowiecki-wezel-janow/
        m = re.search(r'/odcinki/(\d+)-(.+?)/?$', href)
        if not m:
            continue

        zone_id_num = m.group(1)
        slug = m.group(2)

        # Ustal pełny URL
        url = href if href.startswith('http') else f"https://odcinkowy.pl{href}"

        zones.append({
            '_id_num': zone_id_num,
            '_slug':   slug,
            '_url':    url,
        })

    log.info(f"Znaleziono {len(zones)} odcinków na liście")
    return zones


def scrape_zone_detail(zone_stub: dict) -> dict | None:
    """
    Pobierz szczegóły jednej strefy OPP ze strony odcinkowy.pl.
    Zwraca słownik gotowy do wstawienia do zones.json.
    """
    url = zone_stub['_url']
    time.sleep(SLEEP_BETWEEN)
    soup = get_soup(url)
    if not soup:
        return None

    try:
        # Nazwa strefy — zazwyczaj w <h1>
        h1 = soup.find('h1')
        name = h1.get_text(strip=True) if h1 else zone_stub['_slug'].replace('-', ' ').title()

        # Droga (np. "A4", "S7", "DK50")
        road = ''
        for tag in soup.find_all(['span', 'td', 'li', 'p']):
            t = tag.get_text(strip=True)
            m = re.match(r'^(A\d+|S\d+|DK\d+|DW\d+|DG\d+)$', t)
            if m:
                road = m.group(1)
                break

        # Województwo
        voivodeship = ''
        for tag in soup.find_all(string=re.compile(r'województwo', re.I)):
            parent = tag.parent
            if parent:
                vt = parent.get_text(strip=True).lower()
                for woj in ['mazowieckie','małopolskie','śląskie','dolnośląskie',
                            'wielkopolskie','łódzkie','lubelskie','podkarpackie',
                            'podlaskie','warmińsko-mazurskie','pomorskie',
                            'kujawsko-pomorskie','zachodniopomorskie','lubuskie',
                            'opolskie','świętokrzyskie']:
                    if woj in vt:
                        voivodeship = woj
                        break
            if voivodeship:
                break

        # Limit prędkości
        limit = 120
        for tag in soup.find_all(string=re.compile(r'km/h', re.I)):
            limit = parse_limit(str(tag))
            break

        # Długość
        length_km = 0.0
        for tag in soup.find_all(string=re.compile(r'\d[\d,\.]+\s*km', re.I)):
            v = parse_length(str(tag))
            if v > 0:
                length_km = v
                break

        # Współrzędne — szukaj Google Maps linku lub meta
        start_lat, start_lon = 0.0, 0.0
        end_lat, end_lon     = 0.0, 0.0

        # Próba 1: link do Google Maps z współrzędnymi
        for a in soup.find_all('a', href=re.compile(r'maps\.google|google\.com/maps')):
            href = a.get('href', '')
            coords = re.findall(r'[-\d]+\.[\d]+,[-\d]+\.[\d]+', href)
            if len(coords) >= 2:
                s = coords[0].split(',')
                e = coords[1].split(',')
                start_lat, start_lon = float(s[0]), float(s[1])
                end_lat, end_lon     = float(e[0]), float(e[1])
                break
            elif len(coords) == 1:
                s = coords[0].split(',')
                start_lat, start_lon = float(s[0]), float(s[1])

        # Próba 2: dane JSON-LD lub meta og:
        for script in soup.find_all('script', type='application/ld+json'):
            try:
                ld = json.loads(script.string)
                if 'geo' in str(ld):
                    # uproszczone — wyciągnij pierwsze koordynaty
                    lats = re.findall(r'"latitude":\s*([\d\.]+)', script.string)
                    lons = re.findall(r'"longitude":\s*([\d\.]+)', script.string)
                    if lats and lons:
                        start_lat = float(lats[0])
                        start_lon = float(lons[0])
                    if len(lats) > 1:
                        end_lat = float(lats[1])
                        end_lon = float(lons[1])
            except Exception:
                pass

        # Data aktywacji
        activated = ''
        for tag in soup.find_all(string=re.compile(r'aktywowa|uruchom|włączon', re.I)):
            m = re.search(r'(\d{4}-\d{2}-\d{2})', str(tag.parent))
            if m:
                activated = m.group(1)
                break

        zone = {
            "id":           f"OPP_{road}_{zone_stub['_id_num']:>03}",
            "name":         name,
            "road":         road,
            "voivodeship":  voivodeship,
            "limit":        limit,
            "start":        {"lat": start_lat, "lon": start_lon},
            "end":          {"lat": end_lat,   "lon": end_lon},
            "length_km":    length_km,
            "direction":    "both",
            "_source_url":  url,
        }
        if activated:
            zone["activated"] = activated

        log.info(f"  ✓ {name[:60]} ({road}, {limit} km/h, {length_km} km)")
        return zone

    except Exception as e:
        log.warning(f"Błąd parsowania {url}: {e}")
        return None


# ── Merging ───────────────────────────────────────────────────

def merge_zones(existing: list[dict], scraped: list[dict]) -> tuple[list[dict], int]:
    """
    Połącz istniejące strefy z nowo zescrapowanymi.
    Priorytet:
      - Zachowaj ręcznie zweryfikowane koordynaty (start.lat != 0)
      - Dodaj nowe strefy
      - Zaktualizuj limit/długość jeśli się zmieniły
    Zwraca (merged_list, liczba_zmian).
    """
    # Indeks istniejących po ID
    existing_by_id = {z['id']: z for z in existing}
    changes = 0
    merged  = list(existing)  # zacznij od kopii

    for scraped_zone in scraped:
        sid = scraped_zone['id']

        if sid not in existing_by_id:
            # Nowa strefa
            log.info(f"  + NOWA strefa: {scraped_zone['name']}")
            merged.append(scraped_zone)
            changes += 1
        else:
            # Istniejąca — sprawdź zmiany w limicie i długości
            existing_zone = existing_by_id[sid]
            updated = False

            if (scraped_zone['limit'] != existing_zone.get('limit')
                    and scraped_zone['limit'] > 0):
                log.info(f"  ~ Zmiana limitu {sid}: "
                         f"{existing_zone.get('limit')} → {scraped_zone['limit']}")
                existing_zone['limit'] = scraped_zone['limit']
                updated = True

            if (scraped_zone['length_km'] > 0
                    and abs(scraped_zone['length_km']
                            - existing_zone.get('length_km', 0)) > 0.2):
                log.info(f"  ~ Zmiana długości {sid}: "
                         f"{existing_zone.get('length_km')} → {scraped_zone['length_km']}")
                existing_zone['length_km'] = scraped_zone['length_km']
                updated = True

            # Uzupełnij współrzędne jeśli brakuje
            if (existing_zone.get('start', {}).get('lat', 0) == 0
                    and scraped_zone['start']['lat'] != 0):
                existing_zone['start'] = scraped_zone['start']
                existing_zone['end']   = scraped_zone['end']
                updated = True

            if updated:
                changes += 1

    return merged, changes


# ── Główna funkcja ────────────────────────────────────────────

def main():
    log.info("═══ OPP Zones Updater ═══")
    log.info(f"Plik docelowy: {ZONES_FILE}")

    # Wczytaj aktualne dane
    data = load_existing()
    existing_zones = data.get('zones', [])
    log.info(f"Aktualna liczba stref: {len(existing_zones)}")

    # Scrape listy stref
    zone_stubs = scrape_zone_list()
    if not zone_stubs:
        log.error("Nie udało się pobrać listy stref — przerywam")
        return

    # Scrape szczegółów każdej strefy
    scraped_zones = []
    for i, stub in enumerate(zone_stubs):
        log.info(f"[{i+1}/{len(zone_stubs)}] Pobieranie: {stub['_url']}")
        detail = scrape_zone_detail(stub)
        if detail:
            scraped_zones.append(detail)

    log.info(f"Zescrapowano {len(scraped_zones)} stref")

    # Merge
    merged, changes = merge_zones(existing_zones, scraped_zones)

    if changes == 0:
        log.info("Brak zmian — zones.json jest aktualny")
    else:
        log.info(f"Wykryto {changes} zmian — zapisuję zones.json")
        data['zones']   = merged
        data['source']  = 'CANARD/GITD + odcinkowy.pl (auto-scrape)'
        data['version'] = '2.1'
        save(data)

    log.info("═══ Koniec ═══")


if __name__ == '__main__':
    main()
