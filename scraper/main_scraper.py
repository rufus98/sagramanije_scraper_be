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
from scraper_sagre import scrape_events as scrape_sagr
from scraper import main as main_trovasagre

_geocode_cache = {}

def estrai_ora_inizio(*testi: str | None) -> str | None:
    """
    Cerca il primo orario "HH:MM" nel primo testo libero non vuoto tra
    quelli passati (es. program_notes/description di trovasagre.com,
    description di sagr.it), che di solito corrisponde all'orario di
    apertura della prima giornata. Nessuna fonte espone un campo
    "ora_inizio" strutturato, quindi questa è un'estrazione best-effort:
    può mancare (nessun orario nel testo) o essere imprecisa.
    """
    for testo in testi:
        if not testo:
            continue
        match = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", testo)
        if match:
            ora, minuti = match.groups()
            return f"{int(ora):02d}:{minuti}"
    return None

def merge_missing_keys(json_a, json_b, keys):
    """
    Aggiunge in A gli elementi presenti in B ma non trovati
    confrontando solo le chiavi indicate.

    Confronto O(n+m) tramite set di chiavi: il doppio ciclo annidato
    precedente (O(n*m)) con decine di migliaia di elementi per lato
    richiedeva centinaia di milioni di confronti ed era il vero collo
    di bottiglia dello scraper.
    """
    def chiave(item):
        return tuple(item.get(k) for k in keys)

    result = deepcopy(json_a)
    chiavi_esistenti = {chiave(item) for item in result}

    aggiunti = 0
    for item_b in json_b:
        k = chiave(item_b)
        if k not in chiavi_esistenti:
            result.append(deepcopy(item_b))
            chiavi_esistenti.add(k)
            aggiunti += 1

    print(f"merge_missing_keys: {aggiunti} elementi aggiunti da B, {len(result)} totali")
    return result

def get_coordinates(city_name, province_code=None, country="Italy"):
    """
    Restituisce (lat, lon) come float, oppure (None, None) se non trovate.
    Usa Nominatim (OpenStreetMap) - gratuito ma limitato a 1 richiesta/secondo.
    """
    if not city_name:
        return (None, None)

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

def _provincia_da_address(address: dict) -> str | None:
    """Estrae la sigla provincia (es. "CH") dal campo ISO3166-2-lvl6 di Nominatim ("IT-CH")."""
    iso = address.get("ISO3166-2-lvl6") or ""
    if iso.upper().startswith("IT-"):
        return iso.split("-", 1)[1].upper()
    return None

_location_info_cache = {}

def get_location_info(city_name, province_code=None, region=None, lat=None, lon=None, country="Italy"):
    """
    Completa provincia/regione (e coordinate se mancanti) via Nominatim
    (addressdetails=1 espone anche "state" = regione e "ISO3166-2-lvl6" =
    sigla provincia). Non sovrascrive mai un valore già presente in
    ingresso: viene usato solo per riempire i buchi.

    Cerca per nome città quando disponibile (query cacheata per città:
    su decine di migliaia di eventi le città distinte sono poche centinaia,
    quindi quasi tutte le chiamate finiscono in cache). Il reverse geocoding
    per coordinate è usato solo come fallback quando manca il nome città:
    cacheare per lat/lon esatte non aiuta perché ogni evento ha coordinate
    leggermente diverse, quindi userebbe una chiamata Nominatim (1
    richiesta/secondo) per praticamente ogni evento.
    """
    have_coords = lat is not None and lon is not None
    if not city_name and not have_coords:
        return {"lat": lat, "lon": lon, "provincia": province_code, "regione": region}

    use_reverse = not city_name and have_coords
    if use_reverse:
        cache_key = f"rev:{round(float(lat), 3)},{round(float(lon), 3)}"
    else:
        parts = [city_name]
        if province_code:
            parts.append(province_code)
        elif region:
            parts.append(region)
        parts.append(country)
        cache_key = f"fwd:{', '.join(parts)}"

    if cache_key in _location_info_cache:
        found = _location_info_cache[cache_key]
    else:
        headers = {"User-Agent": "sagr-scraper/1.0 (tuaemail@esempio.com)"}
        found = {"lat": None, "lon": None, "provincia": None, "regione": None}
        try:
            if use_reverse:
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/reverse",
                    params={"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1},
                    headers=headers, timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                if data and "address" in data:
                    address = data["address"]
                    found = {
                        "lat": float(lat), "lon": float(lon),
                        "provincia": _provincia_da_address(address),
                        "regione": address.get("state"),
                    }
            else:
                resp = requests.get(
                    "https://nominatim.openstreetmap.org/search",
                    params={"q": ", ".join(parts), "format": "jsonv2", "limit": 1, "addressdetails": 1},
                    headers=headers, timeout=10,
                )
                resp.raise_for_status()
                data = resp.json()
                if data:
                    address = data[0].get("address", {})
                    found = {
                        "lat": float(data[0]["lat"]), "lon": float(data[0]["lon"]),
                        "provincia": _provincia_da_address(address),
                        "regione": address.get("state"),
                    }
        except requests.RequestException as e:
            print(f"Errore geocoding per '{city_name}': {e}")

        _location_info_cache[cache_key] = found
        time.sleep(1)  # Nominatim richiede max 1 richiesta al secondo

    return {
        "lat": lat if have_coords else found.get("lat"),
        "lon": lon if have_coords else found.get("lon"),
        "provincia": province_code or found.get("provincia"),
        "regione": region or found.get("regione"),
    }

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

    citta = event.get("city") or city
    provincia = event.get("province_code") or province
    # trovasagre.com non espone la regione: la ricaviamo via geocoding
    # (reverse se lat/lon sono già noti dall'API, altrimenti forward per città).
    info = get_location_info(
        citta, province_code=provincia, region=None,
        lat=event.get("lat"), lon=event.get("lng"),
    )
    return {
        "nome_sagra": event.get("title"),
        "data_inizio": event.get("date_start"),
        "data_fine": event.get("date_end"),
        "citta": citta,
        "provincia": info["provincia"],
        "regione": info["regione"],
        "lat": info["lat"],
        "leng": info["lon"],
        "locandina": f"https://trovasagre.com{flyer_path}" if flyer_path else None,
        "link_pagina_ufficiale": event.get("scrape_url"),
        "category":"sagra",
        "descrizione": event.get("description"),
        "ora_inizio": estrai_ora_inizio(event.get("program_notes"), event.get("description")),
    }

def build_sagr(event: dict) -> dict:
    # "regione" arriva sempre dalla navigazione del sito (vedi scraper_sagre.py);
    # "provincia" (sigla, es. "CH") è estratta dall'indirizzo della pagina
    # evento quando presente (solo con --details). Se manca, la ricaviamo
    # via geocoding invece di usare la regione come fallback grezzo.
    comune = event.get("comune")
    regione = event.get("region")
    info = get_location_info(
        comune, province_code=event.get("provincia"), region=regione,
        lat=event.get("lat"), lon=event.get("lon"),
    )
    print(event.get("title"))
    return {
        "nome_sagra": event.get("title"),
        "data_inizio": event.get("date_start"),
        "data_fine": event.get("date_end"),
        "citta": comune,
        "provincia": info["provincia"],
        "regione": info["regione"],
        "lat": info["lat"],
        "leng": info["lon"],
        "locandina": None,
        "link_pagina_ufficiale": event.get("url"),
        "category":"sagra",
        "descrizione": event.get("description"),
        # sagr.it non espone un orario di apertura strutturato: proviamo comunque
        # un'estrazione best-effort dalla descrizione (spesso assente per questa fonte).
        "ora_inizio": estrai_ora_inizio(event.get("description")),
    }

def main():

    trovasagre = main_trovasagre()
    # details=True: scarica anche la pagina di ogni evento sagr.it, necessaria
    # per popolare descrizione/coordinate (senza sono sempre None).
    main_sagre = scrape_sagr(details=True)
    print('inizio formattazione campi e lettura geolocalizzazione')
    print('inizio con sito sagr')
    main_sagre = [build_sagr(asdict(event)) for event in main_sagre]
    print('Fine sito sagr')
    print('Inizio sito Trova Sagre')
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