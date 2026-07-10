"""
Modello ORM della tabella `sagre` su MySQL, allineato al formato:

{
    "nome_sagra": "Festa dei Pescatori",
    "data_inizio": "2026-07-09",
    "data_fine": "2026-07-13",
    "citta": "Santa Croce (trieste)",
    "provincia": "TS",
    "lat": 45.7384032,
    "leng": 13.6889939,
    "locandina": "https://...jpg",
    "link_pagina_ufficiale": "https://...",
    "category": "sagra"
}
"""
import hashlib

from sqlalchemy import Column, Integer, String, Float, Text, Index

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
    lat = Column(Float, nullable=True, index=True)
    leng = Column(Float, nullable=True, index=True)
    locandina = Column(Text, nullable=True)
    link_pagina_ufficiale = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)

    __table_args__ = (
        # Indice composito: velocizza il pre-filtro per bounding box
        # (range di lat/leng) usato prima del calcolo Haversine preciso.
        Index("ix_sagre_lat_leng", "lat", "leng"),
    )