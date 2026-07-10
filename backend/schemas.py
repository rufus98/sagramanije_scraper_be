from typing import Optional
from pydantic import BaseModel


class SagraEvent(BaseModel):
    nome_sagra: str
    data_inizio: Optional[str] = None
    data_fine: Optional[str] = None
    citta: str
    provincia: Optional[str] = None
    lat: Optional[float] = None
    leng: Optional[float] = None
    locandina: Optional[str] = None
    link_pagina_ufficiale: Optional[str] = None
    category: Optional[str] = None


class SagraEventWithDistance(SagraEvent):
    distanza_km: float


class OttimizzaRequest(BaseModel):
    events: list[SagraEvent]


class OttimizzaResponse(BaseModel):
    stats: dict
    events: list[SagraEvent]


class VicineResponse(BaseModel):
    lat: float
    leng: float
    raggio_km: float
    totale_trovati: int
    risultati: list[SagraEventWithDistance]