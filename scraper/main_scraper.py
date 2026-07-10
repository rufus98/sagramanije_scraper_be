import argparse
from copy import deepcopy
import math
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

def main():

    trovasagre = main_trovasagre()
    main_sagre = main_sagr()
    print('trovasagre')
    print(trovasagre)
    print('main_sagre')
    print(main_sagre)
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