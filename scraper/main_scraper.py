import argparse
from copy import deepcopy
from dataclasses import asdict
import math
import re
import time
import json
import requests
import pandas as pd
from urllib.parse import urljoin, urlparse
import urllib.robotparser
from scraper_sagre import main as main_sagr
from scraper import main as main_trovasagre
from scraper_sagre import main as main_s
import time
import requests

_geocode_cache = {}

def estrai_ora_inizio(testo: str | None) -> str | None:
    """
    Cerca il primo orario "HH:MM" in un testo libero (es. il program_notes
    di trovasagre.com), che di solito corrisponde all'orario di apertura
    della prima giornata. Nessuna fonte espone un campo "ora_inizio"
    strutturato, quindi questo è un'estrazione best-effort: può mancare
    (nessun orario nel testo) o essere impreciso.
    """
    if not testo:
        return None
    match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", testo)
    if not match:
        return None
    ora, minuti = match.groups()
    return f"{int(ora):02d}:{minuti}"

def merge_missing_keys(json_a,json_b, keys):
    """
    Aggiunge in A gli elementi presenti in B ma non trovati
    confrontando solo le chiavi indicate.
    """
    result = deepcopy(json_a)
    print('test inizio')

    for item_b in json_b:
        trovato = False

        for item_a in result:
            if all(item_a.get(k) == item_b.get(k) for k in keys):
                trovato = True
                break

        if not trovato:
            result.append(deepcopy(item_b))
    print('test fine')
    return result

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

def fill_missing_coordinates(records):
    """
    Cicla i record e, per quelli senza lat/leng, li recupera geocodificando
    citta + provincia.
    """
    for record in records:
        if record.get("lat") and record.get("leng"):
            continue

        citta = record.get("citta")
        if not citta:
            continue

        lat, lon = get_coordinates(citta, record.get("provincia"))
        record["lat"] = lat
        record["leng"] = lon
        print('test')
    return records

def del_duplicate(json):
    df = pd.read_json("eventi.json")
    df_dedup = df.drop_duplicates(subset="title", keep="first")
    df_dedup.to_json("eventi_dedup.json", orient="records", force_ascii=False, indent=2)
def get_coordinates_trovasagre(city_name, province_code=None, country="Italy"):
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

def extract_city_province(location):
    if not location:
        return None, None

    location = location.strip()
    match = re.search(r"\(([A-Za-z]{2})\)\s*$", location)
    if not match:
        return location, None

    province = match.group(1).upper()
    city = location[:match.start()].strip().rstrip(",").strip()
    return (city or location), province

def build_trovasagre(event: dict) -> dict:
    flyer_path = event.get("flyer_path")
    province = None
    if event.get("province_code") is None:
        match = re.search(r"\(([^)]+)\)", event.get("location") or "")
        if match:
            province = match.group(1)
    if event.get("city") is None:
        city, province_estratta = extract_city_province(event.get("location"))
        province = province or province_estratta

    if event.get("lat") is None and event.get("leng") is None:
        lat, lon = get_coordinates_trovasagre(event.get("city") or city,  event.get("province_code") or province)
    return {
        "nome_sagra": event.get("title"),
        "data_inizio": event.get("date_start"),
        "data_fine": event.get("date_end"),
        "citta": event.get("city") or city,
        "provincia": event.get("province_code") or province,
        "lat": event.get("lat") or lat,
        "leng": event.get("lng") or lon,
        "locandina": f"https://trovasagre.com{flyer_path}" if flyer_path else None,
        "link_pagina_ufficiale": event.get("scrape_url"),
        "category":"sagra",
        "descrizione": event.get("description"),
        "ora_inizio": estrai_ora_inizio(event.get("program_notes")),
    }

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

def build_sagr(event: dict) -> dict:

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
        "category":"sagra",
        # sagr.it non espone un orario strutturato -> ora_inizio resta nullo.
        # La descrizione è popolata solo se lo scraper sagr.it viene lanciato
        # con --details (scarica anche la pagina di ogni evento).
        "descrizione": event.get("description"),
        "ora_inizio": None,
    }

def main():

    trovasagre = main_trovasagre()
    main_sagre = main_sagr()
    main_sagre = [build_sagr(asdict(event)) for event in main_sagre]
    trovasagre = [build_trovasagre(event) for event in trovasagre]



    with open('/app/output/trovasagre.json', "w", encoding="utf-8") as f:
        json.dump(trovasagre, f, ensure_ascii=False, indent=2)
    with open('/app/output/main_sagre.json', "w", encoding="utf-8") as f:
        json.dump(main_sagre, f, ensure_ascii=False, indent=2)
    unito = merge_missing_keys(trovasagre, main_sagre, ["nome_sagra", "citta", "provincia"])
    with open('/app/output/trovasagre2.0.json', "w", encoding="utf-8") as f:
        json.dump(unito, f, ensure_ascii=False, indent=2)
    
    

    print(f"Salvato in {'/app/output/trovasagre2.0.json'} ({len(unito)} record)")



if __name__ == "__main__":
    main()