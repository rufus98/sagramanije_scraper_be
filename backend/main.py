"""
API FastAPI per gestione sagre, con persistenza su MySQL.

1. POST /sagre/ottimizza
   Prende un JSON di eventi, geocodifica solo le città senza lat/leng
   (deduplicate + cache persistente su tabella `geocode_cache`), e fa
   upsert nella tabella `sagre` (nessun duplicato anche se rimandi
   più volte lo stesso evento).

2. GET /sagre/vicine
   Data una coordinata (lat, leng) e un raggio in km, interroga il DB
   e restituisce le sagre entro quel raggio, ordinate per distanza.
"""
import json
import math
import os
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import Base, engine, get_db
from db_models import SagraDB, make_event_key
from geocoding import enrich_events_with_coordinates
from distance import haversine_km
from schemas import OttimizzaRequest, OttimizzaResponse, VicineResponse, SagraEventWithDistance, SagraEvent

# Percorso di default del file JSON da importare via GET /sagre/importa-file
# (montato come volume nel container, vedi docker-compose.yml)
DEFAULT_IMPORT_PATH = os.getenv("IMPORT_JSON_PATH", "/app/trovasagre2.0.json")

app = FastAPI(
    title="Sagre API",
    description="API per ottimizzare (geocoding) e interrogare geograficamente un dataset di sagre italiane, con persistenza MySQL.",
    version="2.1.1",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """
    Crea le tabelle se non esistono già. MySQL può impiegare qualche
    secondo in più a essere pronto rispetto a quanto rilevato
    dall'healthcheck di Docker (soprattutto al primissimo avvio, quando
    inizializza la data directory) — per questo si riprova con backoff
    invece di fallire subito con "Connection refused".
    """
    max_tentativi = 15
    attesa_secondi = 2

    for tentativo in range(1, max_tentativi + 1):
        try:
            Base.metadata.create_all(bind=engine)
            print(f"Connessione al DB riuscita al tentativo {tentativo}.")
            return
        except OperationalError as e:
            print(f"Tentativo {tentativo}/{max_tentativi}: DB non ancora pronto ({e.__cause__ or e}). Riprovo tra {attesa_secondi}s...")
            time.sleep(attesa_secondi)

    raise RuntimeError("Impossibile connettersi al database MySQL dopo diversi tentativi.")


# Un grado di latitudine è ~111 km ovunque; per la longitudine dipende dal
# coseno della latitudine, ma per un pre-filtro approssimato va benissimo
# una stima larga (più larga del necessario, mai troppo stretta).
KM_PER_DEGREE = 111.0


def _importa_eventi(db: Session, events: list[dict]) -> dict:
    """
    Geocodifica ciò che manca e fa upsert nel database. Ogni evento è
    identificato univocamente da nome_sagra+citta+data_inizio, quindi
    rilanciare la stessa importazione non crea duplicati: aggiorna solo
    i campi cambiati. Condivisa da /sagre/ottimizza e /sagre/importa-file.
    """
    result = enrich_events_with_coordinates(db, events)

    # Traccia anche gli oggetti appena aggiunti in questo stesso batch: la
    # sessione ha autoflush=False, quindi una query sul DB non "vede" un
    # db.add() precedente ancora in sospeso. Senza questa cache, due eventi
    # con la stessa chiave nello stesso file (es. dati duplicati da fonti
    # diverse) genererebbero due INSERT e un IntegrityError sulla chiave unica.
    pendenti: dict[str, SagraDB] = {}

    for ev in result["events"]:
        key = make_event_key(ev["nome_sagra"], ev["citta"], ev.get("data_inizio"))
        obj = pendenti.get(key) or db.query(SagraDB).filter(SagraDB.event_key == key).first()

        if obj:
            for field, value in ev.items():
                setattr(obj, field, value)
        else:
            obj = SagraDB(event_key=key, **ev)
            db.add(obj)

        pendenti[key] = obj

    db.commit()

    return result


@app.post("/sagre/ottimizza", response_model=OttimizzaResponse)
def ottimizza_dataset(payload: OttimizzaRequest, db: Session = Depends(get_db)):
    """
    Riceve una lista di eventi (anche con lat/leng nulli), geocodifica
    solo ciò che manca (deduplicato per citta+provincia) e fa upsert
    nel database. Ogni evento è identificato univocamente da
    nome_sagra+citta+data_inizio, quindi rilanciare la stessa importazione
    non crea duplicati: aggiorna solo i campi cambiati.
    """
    # "id" è generato dal DB: va escluso dall'input, altrimenti l'upsert
    # proverebbe a scrivere id=None sulla chiave primaria di righe esistenti.
    events = [e.model_dump(exclude={"id"}) for e in payload.events]

    if not events:
        raise HTTPException(status_code=400, detail="La lista di eventi è vuota.")

    result = _importa_eventi(db, events)

    return OttimizzaResponse(stats=result["stats"], events=result["events"])


@app.get("/sagre/importa-file", response_model=OttimizzaResponse)
def importa_da_file(
    path: str = Query(DEFAULT_IMPORT_PATH, description="Percorso del file JSON da importare (dentro al container)"),
    db: Session = Depends(get_db),
):
    """
    Legge un file JSON di eventi dal filesystem (es. l'output dello
    scraper) e lo importa nel DB con la stessa logica di /sagre/ottimizza
    (geocoding + upsert).
    """
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"File non trovato: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw_events = json.load(f)

    try:
        events = [SagraEvent(**ev).model_dump(exclude={"id"}) for ev in raw_events]
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=f"JSON non valido: {e}")

    if not events:
        raise HTTPException(status_code=400, detail="Il file JSON non contiene eventi.")

    result = _importa_eventi(db, events)

    return OttimizzaResponse(stats=result["stats"], events=result["events"])


def _to_evento_con_distanza(ev: SagraDB, distanza_km: Optional[float]) -> SagraEventWithDistance:
    return SagraEventWithDistance(
        id=ev.id,
        nome_sagra=ev.nome_sagra,
        data_inizio=ev.data_inizio,
        data_fine=ev.data_fine,
        citta=ev.citta,
        provincia=ev.provincia,
        lat=ev.lat,
        leng=ev.leng,
        locandina=ev.locandina,
        link_pagina_ufficiale=ev.link_pagina_ufficiale,
        category=ev.category,
        descrizione=ev.descrizione,
        ora_inizio=ev.ora_inizio,
        distanza_km=distanza_km,
    )


@app.get("/sagre/vicine", response_model=VicineResponse)
def sagre_vicine(
    lat: Optional[float] = Query(None, description="Latitudine dell'utente (omettila insieme a leng per avere tutti gli eventi)"),
    leng: Optional[float] = Query(None, description="Longitudine dell'utente (omettila insieme a lat per avere tutti gli eventi)"),
    raggio_km: Optional[float] = Query(None, gt=0, description="Raggio di ricerca in km (obbligatorio se lat/leng sono forniti)"),
    limit: int = Query(100, gt=0, le=1000, description="Numero massimo di risultati"),
    db: Session = Depends(get_db),
):
    if (lat is None) != (leng is None):
        raise HTTPException(status_code=400, detail="lat e leng vanno forniti insieme, oppure omessi entrambi.")

    if lat is None:
        eventi = db.query(SagraDB).limit(limit).all()
        risultati = [_to_evento_con_distanza(ev, None) for ev in eventi]
        return VicineResponse(
            lat=None,
            leng=None,
            raggio_km=None,
            totale_trovati=len(risultati),
            risultati=risultati,
        )

    if raggio_km is None:
        raise HTTPException(status_code=400, detail="raggio_km è obbligatorio quando lat e leng sono forniti.")

    delta_lat = raggio_km / KM_PER_DEGREE
    delta_leng = raggio_km / (KM_PER_DEGREE * max(0.1, abs(math.cos(math.radians(lat)))))
    candidati = (
        db.query(SagraDB)
        .filter(
            SagraDB.lat.isnot(None),
            SagraDB.leng.isnot(None),
            and_(
                SagraDB.lat.between(lat - delta_lat, lat + delta_lat),
                SagraDB.leng.between(leng - delta_leng, leng + delta_leng),
            ),
        )
        .all()
    )

    risultati = []
    for ev in candidati:
        dist = haversine_km(lat, leng, ev.lat, ev.leng)
        if dist <= raggio_km:
            risultati.append(_to_evento_con_distanza(ev, round(dist, 2)))

    risultati.sort(key=lambda r: r.distanza_km)
    risultati = risultati[:limit]

    return VicineResponse(
        lat=lat,
        leng=leng,
        raggio_km=raggio_km,
        totale_trovati=len(risultati),
        risultati=risultati,
    )


@app.get("/sagre/{sagra_id}", response_model=SagraEvent)
def sagra_singola(sagra_id: int, db: Session = Depends(get_db)):
    """Restituisce il dettaglio di un singolo evento dato il suo id (vedi campo "id" nelle liste)."""
    ev = db.get(SagraDB, sagra_id)
    if ev is None:
        raise HTTPException(status_code=404, detail=f"Nessuna sagra trovata con id={sagra_id}.")

    return SagraEvent(
        id=ev.id,
        nome_sagra=ev.nome_sagra,
        data_inizio=ev.data_inizio,
        data_fine=ev.data_fine,
        citta=ev.citta,
        provincia=ev.provincia,
        lat=ev.lat,
        leng=ev.leng,
        locandina=ev.locandina,
        link_pagina_ufficiale=ev.link_pagina_ufficiale,
        category=ev.category,
        descrizione=ev.descrizione,
        ora_inizio=ev.ora_inizio,
    )


@app.get("/")
def root():
    return {
        "message": "Sagre API attiva (MySQL)",
        "endpoints": [
            "/sagre/ottimizza (POST)",
            "/sagre/importa-file (GET)",
            "/sagre/vicine (GET)",
            "/sagre/{id} (GET)",
            "/docs",
        ],
    }