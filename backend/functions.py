import html
import json
import os
import re
import time
import unicodedata
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from database import Base, engine, get_db
from db_models import SagraDB, make_event_key
from geocoding import enrich_events_with_coordinates
from distance import haversine_km
from schemas import OttimizzaRequest, OttimizzaResponse, VicineResponse, SagraEventWithDistance, SagraEvent

def build_trovasagre(event: dict) -> dict:
    data_inizio, data_fine = parse_it_date_range(event.get("date") or "")
    geo = event.get("geo") or {}

    return {
        "nome_sagra": event.get("title"),
        "data_inizio": data_inizio,
        "data_fine": data_fine,
        "citta": geo.get("city"),
        "provincia": geo.get("state_short"),
        "regione":geo.get("state"),
        "lat": geo.get("lat"),
        "leng": geo.get("lng"),
        "locandina": event.get("image"),
        "link_pagina_ufficiale": event.get("permalink"),
        "category":"sagra",
        "descrizione": event.get("description"),
        "ora_inizio": None,
    }   
MONTHS_IT = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}


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

def _estrai_descrizione_da_permalink(url: Optional[str]) -> Optional[str]:
    """
    Scarica la pagina ufficiale dell'evento (permalink) ed estrae la
    descrizione dal JSON-LD (schema.org/Event, con fallback su
    meta og:description) — sagreautentiche.json non porta una descrizione
    (il campo "excerpt" è sempre vuoto alla fonte).
    """
    if not url:
        return None

    headers = {"User-Agent": "sagr-scraper/1.0 (tuaemail@esempio.com)"}
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Errore scaricando {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        candidati = data if isinstance(data, list) else [data]
        espansi = []
        for c in candidati:
            if isinstance(c, dict) and "@graph" in c:
                espansi.extend(c["@graph"])
            else:
                espansi.append(c)
        for c in espansi:
            if isinstance(c, dict) and c.get("@type") in ("Event", "Festival"):
                desc = c.get("description")
                if isinstance(desc, str) and desc.strip():
                    return html.unescape(re.sub(r"\s+", " ", desc).strip())

    meta = soup.select_one('meta[property="og:description"]')
    if meta and meta.get("content"):
        return html.unescape(re.sub(r"\s+", " ", meta["content"]).strip())

    return None


def _normalizza_per_confronto(valore) -> str:
    """
    Normalizza una stringa per confronti tolleranti a differenze non
    semantiche tra fonti diverse: spazi superflui, maiuscole/minuscole,
    apici tipografici diversi (’ ‘ ` vs ') e forme Unicode equivalenti
    (NFKC). nome_sagra/citta/provincia/regione sono testo libero preso da
    scraper diversi: un confronto SQL con "==" li tratterebbe come eventi
    distinti anche quando sono lo stesso evento scritto in modo leggermente
    diverso, causando doppioni.
    """
    if not valore:
        return ""
    testo = unicodedata.normalize("NFKC", str(valore))
    testo = re.sub(r"[’‘`´]", "'", testo)
    testo = re.sub(r"\s+", " ", testo).strip()
    return testo.casefold()


def _sagra_gia_presente(db: Session, ev: dict) -> bool:
    """
    Controlla la presenza in DB in base a nome_sagra, data_inizio,
    data_fine, citta, provincia, regione, con confronto tollerante (vedi
    _normalizza_per_confronto). Le date restano un confronto SQL esatto
    (stringhe ISO "YYYY-MM-DD", niente da normalizzare) per restringere i
    candidati senza dover scaricare l'intera tabella.
    """
    candidati = (
        db.query(SagraDB)
        .filter(
            SagraDB.data_inizio == ev.get("data_inizio"),
            SagraDB.data_fine == ev.get("data_fine"),
        )
        .all()
    )
    if not candidati:
        return False

    chiave = tuple(
        _normalizza_per_confronto(ev.get(campo))
        for campo in ("nome_sagra", "citta", "provincia", "regione")
    )
    return any(
        tuple(
            _normalizza_per_confronto(getattr(c, campo))
            for campo in ("nome_sagra", "citta", "provincia", "regione")
        )
        == chiave
        for c in candidati
    )