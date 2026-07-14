"""
Modello ORM della tabella `sagre` su MySQL, allineato al formato di
trovasagre2.0.bonificato.json:

{
    "nome_sagra": "Festa dei Pescatori",
    "data_inizio": "2026-07-09",
    "data_fine": "2026-07-13",
    "citta": "Santa Croce",
    "provincia": "TS",
    "regione": "Friuli Venezia Giulia",
    "lat": "45.7384032",
    "leng": "13.6889939",
    "locandina": "https://...jpg",
    "link_pagina_ufficiale": "https://...",
    "category": "sagra",
    "descrizione": "...",
    "ora_inizio": "19:00"
}
"""
import hashlib

from sqlalchemy import Column, Integer, String, Text

from database import Base


def make_event_key(nome_sagra: str, citta: str, data_inizio: str | None) -> str:
    """
    Chiave univoca dell'evento, usata per fare upsert senza duplicati
    quando lo stesso JSON viene rimandato più volte (es. scraping periodico).
    """
    raw = f"{nome_sagra}|{citta}|{data_inizio or ''}".lower().strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class SagraDB(Base):
    __tablename__ = "sagre"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_key = Column(String(64), unique=True, nullable=False, index=True)

    nome_sagra = Column(String(255), nullable=False)
    data_inizio = Column(String(20), nullable=True)
    data_fine = Column(String(20), nullable=True)
    citta = Column(String(150), nullable=False)
    provincia = Column(String(100), nullable=True, index=True)
    regione = Column(String(100), nullable=True, index=True)
    # Salvate come stringa (non Float): le fonti scraper le restituiscono in
    # formato misto (numerico da trovasagre.com, stringa da sagr.it) e si
    # preserva la rappresentazione originale. Il filtro/calcolo di distanza
    # in /sagre/vicine le converte in float lato applicazione (vedi main.py).
    lat = Column(String(50), nullable=True)
    leng = Column(String(50), nullable=True)
    locandina = Column(Text, nullable=True)
    link_pagina_ufficiale = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    descrizione = Column(Text, nullable=True)
    # Formato "HH:MM"; non tutte le fonti espongono un orario strutturato,
    # quindi il campo resta spesso nullo (best-effort, vedi scraper).
    ora_inizio = Column(String(20), nullable=True)