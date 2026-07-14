"""
Modulo di geocoding con cache persistente su MySQL (tabella `geocode_cache`).
Usa Nominatim (OpenStreetMap) - gratuito ma limitato a 1 richiesta/secondo.
"""
import re
import time
import requests
from sqlalchemy import Column, String, Float
from sqlalchemy.orm import Session

from database import Base


class GeocodeCache(Base):
    """Tabella di cache: una riga per ogni combinazione citta+provincia già geocodificata."""
    __tablename__ = "geocode_cache"

    key = Column(String(300), primary_key=True)  # "citta|provincia"
    lat = Column(Float, nullable=True)
    leng = Column(Float, nullable=True)


def _pulisci_citta(citta: str) -> str:
    """
    Alcuni nomi città arrivano con annotazioni tra parentesi, es.
    "Santa Croce (trieste)" -> il geocoder trova più facilmente
    "Santa Croce" da solo (il nome tra parentesi spesso è già la
    provincia/comune di riferimento, ridondante per la query).
    """
    return re.sub(r"\s*\([^)]*\)", "", citta).strip()


def get_coordinates(db: Session, citta: str, provincia: str | None, country: str = "Italy") -> tuple[float | None, float | None]:
    """
    Restituisce (lat, leng) come float, oppure (None, None) se non trovate.
    Chiave di cache: citta + provincia, per evitare collisioni tra città
    omonime in province diverse. La cache vive nel DB, quindi è condivisa
    tra tutte le richieste e sopravvive al riavvio del container.
    """
    key = f"{citta}|{provincia or ''}"

    cached = db.get(GeocodeCache, key)
    if cached is not None:
        return (cached.lat, cached.leng)

    citta_pulita = _pulisci_citta(citta)
    query = f"{citta_pulita}, {provincia}, {country}" if provincia else f"{citta_pulita}, {country}"

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1}
    headers = {"User-Agent": "sagr-scraper/1.0 (tuaemail@esempio.com)"}

    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        result = (float(data[0]["lat"]), float(data[0]["lon"])) if data else (None, None)
    except requests.RequestException as e:
        print(f"Errore geocoding per '{citta}, {provincia}': {e}")
        result = (None, None)

    db.add(GeocodeCache(key=key, lat=result[0], leng=result[1]))
    db.commit()
    time.sleep(1)  # rispetta il rate limit di Nominatim
    return result


def enrich_events_with_coordinates(db: Session, events: list[dict]) -> dict:
    """
    Applica get_coordinates solo agli eventi che non hanno già lat/leng,
    e solo una volta per ogni combinazione unica citta+provincia.
    Restituisce un dict con eventi arricchiti e statistiche del processo.
    """
    da_geocodificare = {
        (ev["citta"], ev.get("provincia"))
        for ev in events
        if ev.get("lat") is None or ev.get("leng") is None
    }

    coords_map = {}
    for citta, provincia in da_geocodificare:
        coords_map[(citta, provincia)] = get_coordinates(db, citta, provincia)

    aggiornati = 0
    non_trovati = 0
    for ev in events:
        if ev.get("lat") is None or ev.get("leng") is None:
            lat, leng = coords_map[(ev["citta"], ev.get("provincia"))]
            ev["lat"] = lat
            ev["leng"] = leng
            if lat is None:
                non_trovati += 1
            else:
                aggiornati += 1

    return {
        "events": events,
        "stats": {
            "totale_eventi": len(events),
            "citta_uniche_geocodificate": len(da_geocodificare),
            "eventi_aggiornati": aggiornati,
            "eventi_non_geocodificabili": non_trovati,
        },
    }