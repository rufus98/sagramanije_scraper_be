from datetime import date
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
    # Valorizzato solo in output (vedi GET /sagre/{id}); non fa parte del
    # payload di import, indica se esistono attività collegate (tabella
    # "attivita") senza doverle scaricare tutte a parte.
    ha_attivita: Optional[bool] = None

    @field_validator("lat", "leng", mode="before")
    @classmethod
    def _stringa_vuota_come_nullo(cls, v):
        """
        Tratta una stringa vuota/spazi come None invece di far fallire il
        parsing a float. Nel DB lat/leng sono salvate come stringa (vedi
        db_models.py): righe con valore "" invece di NULL (dati legacy o
        geocoding fallito) altrimenti farebbero fallire con un
        ValidationError l'intera risposta di /sagre/vicine per un solo
        record sporco su migliaia.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v


class SagraEventWithDistance(SagraEvent):
    # None quando /sagre/vicine è chiamata senza lat/leng (nessun filtro di distanza).
    distanza_km: Optional[float] = None
    giorni:Optional[int]= None


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


class AttivitaCreate(BaseModel):
    """Payload per creare un'attività su una sagra (id_sagra arriva dal path, non dal body)."""
    giorno: Optional[date] = None
    ora_inizio: Optional[str] = None
    ora_fine: Optional[str] = None
    titolo: str
    descrizione: Optional[str] = None


class Attivita(AttivitaCreate):
    id: int
    id_sagra: int


class AttivitaGiorno(BaseModel):
    giorno: Optional[date] = None
    attivita: list[Attivita]


class AttivitaListResponse(BaseModel):
    id_sagra: int
    totale: int
    giorni: list[AttivitaGiorno]