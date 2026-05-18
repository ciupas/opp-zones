# Progress Log

## 2026-05-18

### Gotowe
- Przeniesiono pliki do właściwej struktury (`scripts/`, `.github/workflows/`)
- GitHub Actions skonfigurowane i działające (co poniedziałek 6:00 UTC + ręcznie)
- Zaktualizowano akcje do v6 (Node.js 24 native — brak ostrzeżeń)
- Przepisano scraper: `odcinkowy.pl` → `naodcinku.pl` (odcinkowy blokował 403)
- Naprawiono parser: filtr FAQ (`startswith("Odcinek")`), długości z DOTALL regex
- Dodano scraper fotoradarów z OpenStreetMap (Overpass API, 4 regiony)
- Dodano README.md z dokumentacją formatu i przykładami

### Stan bazy (`zones.json`)
- **43 strefy OPP** — A1, A2, A4, A8, S2, S3, S6, S7, S8, S11, S14, S17, S51, S52
- **2014 fotoradarów** — cała Polska, źródło OpenStreetMap

### Do zrobienia (opcjonalnie)
- [ ] Uzupełnić brakujące długości 2 stref S7 (niestandardowy format na stronie)
- [ ] Fotoradary bez limitu (~434 szt.) — dane brakują w OSM
- [ ] Ewentualnie dodać `camerasGeoJSON` do zones.json dla łatwiejszego mapowania
