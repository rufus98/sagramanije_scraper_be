#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sagr_scraper.py
================
Scraper per estrarre tutte le sagre/eventi pubblicati su https://sagr.it/

Strategia di navigazione (verificata manualmente sul sito, luglio 2026):
  1. Homepage  -> elenco delle 20 regioni italiane (sezione "Esplora per Regione")
  2. Pagina regione  (/{regione})              -> elenco delle "aree/territori" della regione
  3. Pagina area      (/{regione}/{area}/eventi) -> elenco COMPLETO degli eventi dell'area
     (verificato: questa pagina non è paginata, mostra tutti gli eventi in un'unica lista,
     anche per aree con 100+ eventi come "Monferrato")
  4. (opzionale, --details) Pagina evento (/{regione}/{area}/eventi/{slug}) -> dettagli extra
     (descrizione, indirizzo, prezzo, coordinate GPS)

Il parsing NON dipende dalle classi CSS (che nei siti Next.js cambiano ad ogni build),
ma dal testo e dagli href dei link, che sono stabili. Le icone Material Symbols vengono
renderizzate come testo (es. "calendar_today", "location_on", "favorite") e vengono
usate come separatori affidabili per estrarre titolo, categoria, date e località da
ogni card evento.

Filtro categoria:
    Il sito espone un filtro `?cat=sagra-gastronomica` / `?cat=fiera-artigianale` ecc., ma
    funziona solo nella preview "Prossimi Appuntamenti" della pagina territorio, NON sulla
    pagina /eventi con l'elenco completo (che lo ignora e restituisce sempre tutto). Per
    questo il filtro viene applicato lato client, dopo aver scaricato l'elenco completo,
    sulla base dell'etichetta categoria mostrata su ogni card evento (Sagra/Fiera/Festival/...).
    Di default lo script tiene SOLO le "Sagra" (cat=sagra-gastronomica). Usa --cat all
    per disattivare il filtro e tenere tutte le categorie.

Uso:
    pip install requests beautifulsoup4 --break-system-packages

    python sagr_scraper.py                      # solo sagre gastronomiche (default), CSV + JSON
    python sagr_scraper.py --cat all             # tutte le categorie (sagre, fiere, festival, ecc.)
    python sagr_scraper.py --regions piemonte,lazio   # solo alcune regioni (utile per test)
    python sagr_scraper.py --details             # scarica anche i dettagli di ogni evento (lento!)
    python sagr_scraper.py --output-dir ./dati    # cartella di output personalizzata
    python sagr_scraper.py --delay 1.0 --workers 3   # più lento ma più "gentile" col sito

Output:
    sagre_sagr_it.csv
    sagre_sagr_it.json
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup
import time
import requests

_geocode_cache = {}
BASE_URL = "https://sagr.it"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SagrScraper/1.0; "
        "+https://sagr.it/robots.txt allows /; educational use)"
    ),
    "Accept-Language": "it-IT,it;q=0.9",
}

# Sezioni non-area che possono comparire come link diretti sotto /{regione}/...
REGION_SUBPATH_EXCLUDE = {
    "eventi",
    "produttori",
    "ricette",
    "prodotti",
    "guida",
    "comunita",
    "sagre",
}

MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}

CATEGORY_WORDS = [
    "Festa patronale", "Evento culturale", "Rievocazione storica",
    "Rievocazione", "Sagra gastronomica", "Sagra", "Fiera artigianale",
    "Fiera", "Festival", "Evento",
]

# Mappa tra lo slug del filtro `?cat=` usato dal sito e l'etichetta categoria
# mostrata su ogni card evento (verificato confrontando le due pagine con e
# senza filtro: cat=fiera-artigianale -> badge "Fiera", quindi per analogia
# cat=sagra-gastronomica -> badge "Sagra").
CATEGORY_FILTER_MAP = {
    "sagra-gastronomica": "Sagra",
    "fiera-artigianale": "Fiera",
    "festival": "Festival",
    "festa-patronale": "Festa patronale",
    "evento-culturale": "Evento culturale",
    "rievocazione": "Rievocazione",
}


@dataclass
class Event:
    title: str
    category: Optional[str]
    date_text: str
    date_start: Optional[str]
    date_end: Optional[str]
    comune: Optional[str]
    region: str
    region_slug: str
    area: str
    area_slug: str
    url: str
    description: Optional[str] = None
    indirizzo: Optional[str] = None
    prezzo: Optional[str] = None
    lat: Optional[str] = None
    lon: Optional[str] = None


class Fetcher:
    """Piccolo wrapper su requests con retry, backoff e rate limiting."""

    def __init__(self, delay: float = 0.5, timeout: int = 20, max_retries: int = 3):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.delay = delay
        self.timeout = timeout
        self.max_retries = max_retries
        self._last_request = 0.0

    def get(self, url: str) -> Optional[str]:
        for attempt in range(1, self.max_retries + 1):
            # rate limiting semplice
            elapsed = time.time() - self._last_request
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_request = time.time()
            try:
                resp = self.session.get(url, timeout=self.timeout)
                if resp.status_code == 200:
                    return resp.text
                if resp.status_code == 404:
                    print(f"  [404] {url}")
                    return None
                print(f"  [HTTP {resp.status_code}] {url} (tentativo {attempt}/{self.max_retries})")
            except requests.RequestException as exc:
                print(f"  [ERRORE] {url} -> {exc} (tentativo {attempt}/{self.max_retries})")
            time.sleep(min(2 ** attempt, 10))
        print(f"  [FALLITO] impossibile scaricare {url}")
        return None


def norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def slug_to_name(slug: str) -> str:
    """Converte uno slug tipo 'langhe-e-roero' in 'Langhe e Roero'."""
    small = {"e", "di", "del", "della", "dei", "delle", "d", "in", "a", "al", "alla"}
    words = slug.replace("-", " ").split()
    out = []
    for i, w in enumerate(words):
        if i > 0 and w in small:
            out.append(w)
        else:
            out.append(w.capitalize())
    return " ".join(out)


def parse_it_date_range(date_text: str) -> tuple[Optional[str], Optional[str]]:
    """
    Converte stringhe tipo:
      '31 maggio 2026'                 -> (2026-05-31, 2026-05-31)
      '27 marzo - 29 marzo 2026'       -> (2026-03-27, 2026-03-29)
      '10 ottobre - 6 dicembre 2026'   -> (2026-10-10, 2026-12-06)
    in date ISO. Ritorna (None, None) se non parsabile.
    """
    if not date_text:
        return None, None
    parts = re.split(r"\s*[-–—]\s*", date_text.strip())

    def parse_single(s: str, fallback_year: Optional[int]) -> Optional[tuple[int, int, int]]:
        m = re.search(r"(\d{1,2})\s+([a-zà-ù]+)(?:\s+(\d{4}))?", s.strip(), re.IGNORECASE)
        if not m:
            return None
        day = int(m.group(1))
        month = MONTHS_IT.get(m.group(2).lower())
        year = int(m.group(3)) if m.group(3) else fallback_year
        if not month or not year:
            return None
        return year, month, day

    if len(parts) == 1:
        d = parse_single(parts[0], None)
        if not d:
            return None, None
        iso = f"{d[0]:04d}-{d[1]:02d}-{d[2]:02d}"
        return iso, iso

    if len(parts) == 2:
        end = parse_single(parts[1], None)
        if not end:
            return None, None
        start = parse_single(parts[0], end[0])
        if not start:
            return None, None
        start_iso = f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}"
        end_iso = f"{end[0]:04d}-{end[1]:02d}-{end[2]:02d}"
        return start_iso, end_iso

    return None, None


def parse_event_anchor(a_tag, region_name: str, region_slug: str,
                        area_name: str, area_slug: str) -> Optional[Event]:
    href = a_tag.get("href", "")
    if not href:
        return None
    text = norm_ws(a_tag.get_text(separator="", strip=True))
    if "calendar_today" not in text or "location_on" not in text:
        return None

    before_cal, rest = text.split("calendar_today", 1)
    date_part, location_part = rest.split("location_on", 1)

    title = None
    category = None
    if "favorite" in before_cal:
        title_cat, title_repeat = before_cal.split("favorite", 1)
        title_cat = title_cat.strip()
        title_repeat = title_repeat.strip()
        if title_repeat:
            title = title_repeat
            if title_cat.startswith(title):
                category = title_cat[len(title):].strip() or None
            else:
                category = title_cat or None
    if title is None:
        candidate = before_cal.strip()
        for cat in CATEGORY_WORDS:
            if candidate.endswith(cat) and len(candidate) > len(cat):
                title = candidate[: -len(cat)].strip()
                category = cat
                break
        if title is None:
            title = candidate or "N/D"

    date_text = date_part.strip()
    location = location_part.strip()
    comune = location.split(",")[0].strip() if location else None

    url = href if href.startswith("http") else BASE_URL + href
    date_start, date_end = parse_it_date_range(date_text)

    return Event(
        title=title,
        category=category,
        date_text=date_text,
        date_start=date_start,
        date_end=date_end,
        comune=comune,
        region=region_name,
        region_slug=region_slug,
        area=area_name,
        area_slug=area_slug,
        url=url,
    )


def get_regions(fetcher: Fetcher) -> list[dict]:
    html = fetcher.get(BASE_URL + "/")
    regions: dict[str, dict] = {}
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            path = href.replace(BASE_URL, "")
            m = re.fullmatch(r"/([a-z-]+)", path)
            if not m:
                continue
            slug = m.group(1)
            text = norm_ws(a.get_text(separator=" ", strip=True))
            mm = re.search(r"(\d+)\s*eventi", text)
            if not mm:
                continue  # non è una card regione (es. /eventi, /ricette, ecc.)
            name = text[: mm.start()].strip() or slug_to_name(slug)
            regions[slug] = {
                "slug": slug,
                "name": name,
                "url": f"{BASE_URL}/{slug}",
                "n_eventi_dichiarati": int(mm.group(1)),
            }

    if regions:
        return sorted(regions.values(), key=lambda r: r["name"])

    # Fallback statico (le 20 regioni italiane, slug stabili su sagr.it)
    print("  [WARN] parsing homepage fallito, uso elenco regioni di fallback")
    fallback_slugs = [
        "piemonte", "valle-d-aosta", "lombardia", "trentino-alto-adige", "veneto",
        "friuli-venezia-giulia", "liguria", "emilia-romagna", "toscana", "umbria",
        "marche", "lazio", "abruzzo", "molise", "campania", "puglia", "basilicata",
        "calabria", "sicilia", "sardegna",
    ]
    return [
        {"slug": s, "name": slug_to_name(s), "url": f"{BASE_URL}/{s}", "n_eventi_dichiarati": None}
        for s in fallback_slugs
    ]


def get_areas(fetcher: Fetcher, region: dict) -> list[dict]:
    html = fetcher.get(region["url"])
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    base = f"/{region['slug']}/"
    seen: dict[str, dict] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        path = href.replace(BASE_URL, "")
        if not path.startswith(base):
            continue
        rest = path[len(base):].strip("/")
        if not rest or "/" in rest or rest in REGION_SUBPATH_EXCLUDE:
            continue
        if rest not in seen:
            seen[rest] = {
                "slug": rest,
                "name": slug_to_name(rest),
                "url": f"{BASE_URL}/{region['slug']}/{rest}/eventi",
            }
    return sorted(seen.values(), key=lambda a: a["name"])


def get_events_for_area(fetcher: Fetcher, region: dict, area: dict) -> list[Event]:
    html = fetcher.get(area["url"])
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    prefix = f"/{region['slug']}/{area['slug']}/eventi/"
    events: dict[str, Event] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        path = href.replace(BASE_URL, "")
        if not path.startswith(prefix):
            continue
        ev = parse_event_anchor(a, region["name"], region["slug"], area["name"], area["slug"])
        if ev:
            events[ev.url] = ev  # dedup per url
    return list(events.values())


def enrich_event_details(fetcher: Fetcher, event: Event) -> Event:
    """Scarica la pagina del singolo evento per estrarre descrizione, indirizzo, prezzo, GPS."""
    html = fetcher.get(event.url)
    if not html:
        return event
    soup = BeautifulSoup(html, "html.parser")

    # descrizione: meta og:description è già un buon riassunto pulito
    meta_desc = soup.find("meta", attrs={"property": "og:description"})
    if meta_desc and meta_desc.get("content"):
        event.description = norm_ws(meta_desc["content"])

    # prezzo: cerca il blocco "Ingresso" seguito dal testo successivo
    text_all = norm_ws(soup.get_text(separator="|"))
    m = re.search(r"Ingresso\|([^|]+)", text_all)
    if m:
        event.prezzo = m.group(1).strip()

    # indirizzo: testo dopo "Luogo"
    m = re.search(r"Luogo\|([^|]+)\|([^|]+)", text_all)
    if m:
        event.indirizzo = norm_ws(m.group(2))

    # coordinate GPS dal link "Indicazioni" verso Google Maps
    dir_link = soup.find("a", href=re.compile(r"destination=-?\d+\.\d+,-?\d+\.\d+"))
    if dir_link:
        m = re.search(r"destination=(-?\d+\.\d+),(-?\d+\.\d+)", dir_link["href"])
        if m:
            event.lat, event.lon = m.group(1), m.group(2)

    return event


def save_csv(events: list[Event], path: Path) -> None:
    if not events:
        return
    fieldnames = list(asdict(events[0]).keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for ev in events:
            writer.writerow(asdict(ev))


def save_json(events: list[Event], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump([asdict(ev) for ev in events], f, ensure_ascii=False, indent=2)

def get_coordinates(city_name, province_code=None, country="Italy"):
    """
    Restituisce (lat, lon) come float, oppure (None, None) se non trovate.
    Usa Nominatim (OpenStreetMap) - gratuito ma limitato a 1 richiesta/secondo.
    """
    query = city_name
    if province_code:
        query += f", {province_code}"
    query += f", {country}"

    if query in _geocode_cache:
        return _geocode_cache[query]

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": "sagr-scraper/1.0 (tuaemail@esempio.com)"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = (float(data[0]["lat"]), float(data[0]["lon"])) if data else (None, None)
    except requests.RequestException as e:
        print(f"Errore geocoding per '{city_name}': {e}")
        result = (None, None)

    _geocode_cache[query] = result
    time.sleep(1)  # Nominatim richiede max 1 richiesta al secondo
    return result

def build_record(event: dict) -> dict:

    if event.get("lat") is None and event.get("leng") is None:
        print('ciao 2 ')
        print(event.get("lat"))
        print(event.get("leng"))
        lat, lon = get_coordinates(event.get("comune"),  event.get("region"))
        print(lat, lon)



    return {
        "nome_sagra": event.get("title"),
        "data_inizio": event.get("date_start"),
        "data_fine": event.get("date_end"),
        "citta": event.get("comune"),
        "provincia": event.get("region"),
        "lat":lat,
        "leng": lon,
        "locandina": None,
        "link_pagina_ufficiale":None,
        "category":"sagra"
    }



def main() -> None:
    parser = argparse.ArgumentParser(description="Scraper eventi/sagre da sagr.it")
    parser.add_argument("--output-dir", default=".", help="Cartella di output (default: cartella corrente)")
    parser.add_argument("--regions", default=None,
                         help="Lista di slug regione separati da virgola per limitare lo scraping (es. piemonte,lazio)")
    parser.add_argument("--delay", type=float, default=0.5, help="Secondi di attesa minimi tra le richieste (default 0.5)")
    parser.add_argument("--workers", type=int, default=4, help="Richieste in parallelo per le pagine area (default 4)")
    parser.add_argument("--details", action="store_true",
                         help="Scarica anche la pagina di ogni singolo evento per dati extra (molto più lento)")
    parser.add_argument("--details-workers", type=int, default=8, help="Richieste in parallelo per i dettagli evento")
    parser.add_argument(
        "--cat", default="sagra-gastronomica",
        help=(
            "Filtra per categoria (default: sagra-gastronomica, cioè solo le 'Sagra'). "
            f"Valori noti: {', '.join(CATEGORY_FILTER_MAP)}. Usa 'all' per non filtrare."
        ),
    )
    args = parser.parse_args()

    out_dir = Path('/app/output')
    out_dir.mkdir(parents=True, exist_ok=True)

    fetcher = Fetcher(delay=args.delay)

    print("== 1/3 Recupero elenco regioni ==")
    regions = get_regions(fetcher)
    if args.regions:
        wanted = {s.strip() for s in args.regions.split(",")}
        regions = [r for r in regions if r["slug"] in wanted]
    print(f"  Trovate {len(regions)} regioni.")

    print("== 2/3 Recupero aree/territori per regione ==")
    region_areas: list[tuple[dict, dict]] = []
    for region in regions:
        areas = get_areas(fetcher, region)
        print(f"  {region['name']}: {len(areas)} aree")
        for area in areas:
            region_areas.append((region, area))

    print(f"== 3/3 Recupero eventi per {len(region_areas)} aree (parallelo x{args.workers}) ==")
    all_events: list[Event] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        # ogni worker ha bisogno di un proprio Fetcher per non condividere lo stato del rate-limiter
        # in modo scorretto tra thread; usiamo comunque un delay per thread.
        fetchers = {i: Fetcher(delay=args.delay) for i in range(args.workers)}
        futures = {}
        for i, (region, area) in enumerate(region_areas):
            f = fetchers[i % args.workers]
            futures[pool.submit(get_events_for_area, f, region, area)] = (region, area)
        done = 0
        for fut in as_completed(futures):
            region, area = futures[fut]
            done += 1
            try:
                events = fut.result()
            except Exception as exc:
                print(f"  [ERRORE] {region['name']}/{area['name']}: {exc}")
                events = []
            all_events.extend(events)
            print(f"  [{done}/{len(region_areas)}] {region['name']} / {area['name']}: {len(events)} eventi")

    # dedup globale per url (uno stesso evento potrebbe comparire in aree limitrofe)
    dedup: dict[str, Event] = {}
    for ev in all_events:
        dedup[ev.url] = ev
    all_events = sorted(dedup.values(), key=lambda e: (e.region, e.area, e.date_start or "", e.title))
    print(f"\nTotale eventi unici trovati (tutte le categorie): {len(all_events)}")

    if args.details:
        print(f"== Extra: dettagli per {len(all_events)} eventi (parallelo x{args.details_workers}) ==")
        with ThreadPoolExecutor(max_workers=args.details_workers) as pool:
            fetchers = {i: Fetcher(delay=args.delay) for i in range(args.details_workers)}
            futures = {
                pool.submit(enrich_event_details, fetchers[i % args.details_workers], ev): ev
                for i, ev in enumerate(all_events)
            }
            done = 0
            for fut in as_completed(futures):
                done += 1
                if done % 50 == 0 or done == len(all_events):
                    print(f"  [{done}/{len(all_events)}] dettagli scaricati")
                try:
                    fut.result()
                except Exception as exc:
                    print(f"  [ERRORE dettagli] {futures[fut].url}: {exc}")
    csv_path = out_dir / "sagre_sagr_it.csv"
    json_path = out_dir / "sagre_sagr_it.json"
    save_json(all_events, json_path)
    records = [build_record(asdict(event)) for event in all_events]

    return records


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.")
        sys.exit(1)