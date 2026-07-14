from typing import Optional
from pydantic import BaseModel


class SagraEvent(BaseModel):
    # Valorizzato solo in output (letto dal DB); ignorato se presente in un payload di import.
    id: Optional[int] = None
    nome_sagra: str
    data_inizio: Optional[str] = None
    data_fine: Optional[str] = None
    citta: str
    provincia: Optional[str] = None
    regione: Optional[str] = None
    # In risposta sono sempre numeriche: Pydantic converte automaticamente
    # una stringa numerica (es. dal DB, dove sono salvate come stringa, vedi
    # db_models.py) in float. In input accetta sia numero che stringa per lo
    # stesso motivo.
    lat: Optional[float] = None
    leng: Optional[float] = None
    locandina: Optional[str] = None
    link_pagina_ufficiale: Optional[str] = None
    category: Optional[str] = None
    descrizione: Optional[str] = None
    ora_inizio: Optional[str] = None


class SagraEventWithDistance(SagraEvent):
    # None quando /sagre/vicine è chiamata senza lat/leng (nessun filtro di distanza).
    distanza_km: Optional[float] = None


class OttimizzaRequest(BaseModel):
    events: list[SagraEvent]


class OttimizzaResponse(BaseModel):
    stats: dict
    events: list[SagraEvent]


class VicineResponse(BaseModel):
    lat: Optional[float] = None
    leng: Optional[float] = None
    raggio_km: Optional[float] = None
    totale_trovati: int
    risultati: list[SagraEventWithDistance]