import argparse
from copy import deepcopy
import math
import re
import time
import json
import requests
from urllib.parse import urljoin, urlparse
import urllib.robotparser
from scraper_sagre import main as main_s
from bs4 import BeautifulSoup
import time
import requests

_geocode_cache = {}
SITE_BASE = "https://trovasagre.com"
BASE_URL = f"{SITE_BASE}/api/events"
LIMIT = 2000
DATE_FROM = "2026-01-01"
DATE_TO = "2026-12-31"
DEFAULT_OUTPUT_FILE = "events.json"
SAGRE_AUTENTICHE = "sagreautentiche.json"
USER_AGENT = "SagreScraperPersonale/1.0 (uso personale, contattami: <tuo@email>)"
DELAY_SECONDS = 2.0          # pausa tra richieste ai siti sorgente
TIMEOUT = 15

_robots_cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

def _get_robot_parser(url: str):
    origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    if origin not in _robots_cache:
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(origin + "/robots.txt")
        try:
            rp.read()
        except Exception:
            rp = None
        _robots_cache[origin] = rp
    return _robots_cache[origin]

def can_fetch(url: str) -> bool:
    rp = _get_robot_parser(url)
    if rp is None:
        print(f"  [attenzione] robots.txt non leggibile per {urlparse(url).netloc}, salto per prudenza.")
        return False
    return rp.can_fetch(USER_AGENT, url)

def fetch_page(page: int) -> dict:
    params = {
        "page": page,
        "limit": LIMIT,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
    }
    response = requests.get(BASE_URL, params=params, timeout=30)
    response.raise_for_status()
    return response.json()
def extract_city_province(location):
    if not location:
        return None, None

    location = location.strip()
    match = re.search(r"\(([A-Za-z]{2})\)\s*$", location)
    if not match:
        return location, None

    province = match.group(1).upper()
    city = location[:match.start()].strip().rstrip(",").strip()
    return (city or location)

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
    flyer_path = event.get("flyer_path")
    province = None
    if event.get("province_code") is None:
        match = re.search(r"\(([^)]+)\)", event.get("location") or "")
        if match:
            province = match.group(1)
    if event.get("city") is None:
        city = extract_city_province(event.get("location"))
    
    if event.get("lat") is None and event.get("leng") is None:
        lat, lon = get_coordinates(event.get("city") or city,  event.get("province_code") or province)



    return {
        "nome_sagra": event.get("title"),
        "data_inizio": event.get("date_start"),
        "data_fine": event.get("date_end"),
        "citta": event.get("city") or city,
        "provincia": event.get("province_code") or province,
        "lat": event.get("lat") or lat,
        "leng": event.get("lng") or lon,
        "locandina": f"{SITE_BASE}{flyer_path}" if flyer_path else None,
        "link_pagina_ufficiale": event.get("scrape_url"),
        "category":"sagra"
    }
def pre_build(event: dict) -> dict:
    geo = event.get("geo")
    if " - " in event.get("date"):
        inizio, fine = event.get("date").split(" - ")
    else:
        inizio = fine = event.get("date") 
    return {
        "nome_sagra": event.get("title"),
        "data_inizio": inizio,
        "data_fine": fine,
        "citta": geo.get("city"),
        "provincia": geo.get("state_short"),
        "lat": geo.get("lat"),
        "leng": geo.get("lng"),
        "locandina": event.get("image"),
        "link_pagina_ufficiale": event.get("scrape_url"),
        "category":"sagra"
    }


def get_page(url: str, session: requests.Session) -> str | None:
    if not can_fetch(url):
        print(f"  [robots.txt] accesso vietato: {url}")
        return None
    try:
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  [errore] {url}: {e}")
        return None
    return resp.text

def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUTPUT_FILE)
    return parser.parse_args()

def sagre_another():
    with open("sagreautentiche.json", "r", encoding="utf-8") as f:
        dati = json.load(f)
    records = [pre_build(event) for event in dati]
    return records

def chiave_multipla(e):
    return (
        e.get("nome_sagra", "").strip().lower(),
        e.get("citta", "").strip().lower(),
        e.get("provincia", "").strip().lower(),
    )
from typing import Callable

def unisci_json(
    base: list[dict],
    secondo: list[dict],
    chiave_fn: Callable[[dict], tuple],
) -> list[dict]:
    """
    Confronta 'base' con 'secondo' e restituisce una nuova lista che
    contiene tutti gli elementi di 'base' più quelli di 'secondo' che non
    erano già presenti in 'base' (secondo la chiave definita da chiave_fn).
    """
    risultato = list(base)  # copia, non modifica l'originale
    chiavi_esistenti = {chiave_fn(elemento) for elemento in base}

    aggiunti = 0
    for elemento in secondo:
        chiave = chiave_fn(elemento)
        if chiave not in chiavi_esistenti:
            risultato.append(elemento)
            chiavi_esistenti.add(chiave)
            aggiunti += 1

    print(f"Elementi aggiunti dal secondo JSON: {aggiunti}")
    return risultato

def merge_missing_keys(json_a,json_b, keys):
    """
    Aggiunge in A gli elementi presenti in B ma non trovati
    confrontando solo le chiavi indicate.
    """
    result = deepcopy(json_a)

    for item_b in json_b:
        trovato = False

        for item_a in result:
            if all(item_a.get(k) == item_b.get(k) for k in keys):
                trovato = True
                break

        if not trovato:
            result.append(deepcopy(item_b))

    return result

def main():
    args = parse_args()

    first_page = fetch_page(1)
    total = first_page["total"]
    actual_limit = first_page["limit"]
    raw_events = list(first_page["events"])
    
    pages = math.ceil(total / actual_limit)
    print(f"Totale eventi: {total} | limit effettivo: {actual_limit} | pagine da scaricare: {pages}")

    for page in range(2, pages + 1):
        print(f"Scarico pagina {page}/{pages}...")
        data = fetch_page(page)
        raw_events.extend(data["events"])
        time.sleep(0.3)

    print(f"Totale eventi scaricati: {len(raw_events)}")
    with open('/app/output/main.json', "w", encoding="utf-8") as f:
        json.dump(raw_events, f, ensure_ascii=False, indent=2)
    records = [build_record(event) for event in raw_events]
    return records
    #test = sagre_another()
    #main_sagre = main_s()
    #print(main_sagre)
    #unito = merge_missing_keys(records, test, ["nome_sagra", "citta", "provincia"])
    #
    #with open(args.output, "w", encoding="utf-8") as f:
    #    json.dump(unito, f, ensure_ascii=False, indent=2)
#
    #print(f"Salvato in {args.output} ({len(unito)} record)")



if __name__ == "__main__":
    main()
