from typing import Optional
from pydantic import BaseModel, field_validator


class SagraEvent(BaseModel):
    # Valorizzato solo in output (letto dal DB); ignorato se presente in un payload di import.
    id: Optional[int] = None
    nome_sagra: str
    data_inizio: Optional[str] = None
    data_fine: Optional[str] = None
    citta: str
    provincia: Optional[str] = None
    regione: Optional[str] = None
    # Salvate come stringa (non float): le fonti scraper le restituiscono
    # in formato misto (numerico da trovasagre.com, stringa da sagr.it) e
    # si vuole preservare esattamente la rappresentazione originale.
    lat: Optional[str] = None
    leng: Optional[str] = None
    locandina: Optional[str] = None
    link_pagina_ufficiale: Optional[str] = None
    category: Optional[str] = None
    descrizione: Optional[str] = None
    ora_inizio: Optional[str] = None

    @field_validator("lat", "leng", mode="before")
    @classmethod
    def _coordinata_come_stringa(cls, v):
        """Converte lat/leng numerici (int/float) in stringa; lascia invariati None e stringhe già presenti."""
        if isinstance(v, (int, float)):
            return str(v)
        return v


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