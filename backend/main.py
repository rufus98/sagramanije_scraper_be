"""
API FastAPI per gestione sagre, con persistenza su MySQL.

1. POST /sagre/ottimizza
   Prende un JSON di eventi, geocodifica solo le città senza lat/leng
   (deduplicate + cache persistente su tabella `geocode_cache`), e fa
   upsert nella tabella `sagre` (nessun duplicato anche se rimandi
   più volte lo stesso evento).

2. GET /sagre/vicine
   Data una coordinata (lat, leng) e un raggio in km, interroga il DB
   e restituisce le sagre entro quel raggio, ordinate per distanza
   (dalla più vicina alla più lontana).
"""
import json
import os
import time
from datetime import date
from typing import Optional

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

# Percorso di default del file JSON da importare via GET /sagre/importa-file
# (montato come volume nel container, vedi docker-compose.yml)
DEFAULT_IMPORT_PATH = os.getenv("IMPORT_JSON_PATH", "/app/trovasagre2.0.bonificato.json")

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


def _importa_eventi(db: Session, events: list[dict]) -> dict:
    """
    Geocodifica ciò che manca e fa upsert nel database. Ogni evento è
    identificato univocamente da nome_sagra+citta+data_inizio, quindi
    rilanciare la stessa importazione non crea duplicati: aggiorna solo
    i campi cambiati. Condivisa da /sagre/ottimizza e /sagre/importa-file.
    """
    result = enrich_events_with_coordinates(db, events)

    # lat/leng sono salvate come stringa (colonna VARCHAR, vedi db_models.py)
    # mentre a questo punto sono float (arrivano già convertite dallo schema
    # Pydantic, che le espone come numeriche): si convertono esplicitamente
    # invece di affidarsi alla coercizione implicita di MySQL.
    for ev in result["events"]:
        if ev.get("lat") is not None:
            ev["lat"] = str(ev["lat"])
        if ev.get("leng") is not None:
            ev["leng"] = str(ev["leng"])

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

    # Il file può arrivare da uno scraping con qualche record incompleto
    # (es. citta non determinabile dalla fonte): un solo evento non valido
    # non deve far fallire l'import di tutti gli altri, quindi si valida
    # evento per evento e si scartano solo quelli malformati, segnalandoli.
    events = []
    scartati = []
    for i, ev in enumerate(raw_events):
        try:
            events.append(SagraEvent(**ev).model_dump(exclude={"id"}))
        except ValidationError as e:
            scartati.append({"indice": i, "nome_sagra": ev.get("nome_sagra"), "errore": str(e)})

    if not events:
        raise HTTPException(status_code=400, detail="Il file JSON non contiene eventi validi.")

    result = _importa_eventi(db, events)
    result["stats"]["eventi_scartati"] = len(scartati)
    if scartati:
        result["stats"]["dettaglio_scartati"] = scartati

    return OttimizzaResponse(stats=result["stats"], events=result["events"])


def _to_evento_con_distanza(ev: SagraDB, distanza_km: Optional[float]) -> SagraEventWithDistance:
    return SagraEventWithDistance(
        id=ev.id,
        nome_sagra=ev.nome_sagra,
        data_inizio=ev.data_inizio,
        data_fine=ev.data_fine,
        citta=ev.citta,
        provincia=ev.provincia,
        regione=ev.regione,
        lat=ev.lat,
        leng=ev.leng,
        locandina=ev.locandina,
        link_pagina_ufficiale=ev.link_pagina_ufficiale,
        category=ev.category,
        descrizione=ev.descrizione,
        ora_inizio=ev.ora_inizio,
        distanza_km=distanza_km,
    )


def _filtro_solo_attive(query, oggi: str):
    """
    Esclude le sagre già concluse, mantenendo quelle in corso (oggi tra
    data_inizio e data_fine) e quelle future (data_inizio nel futuro): basta
    verificare che data_fine (o, in mancanza, data_inizio) non sia già
    passata. Le date sono stringhe ISO "YYYY-MM-DD", quindi il confronto
    lessicografico coincide con quello cronologico. Un evento senza alcuna
    data nota viene comunque incluso (non si può stabilire che sia passato).
    """
    riferimento = func.coalesce(SagraDB.data_fine, SagraDB.data_inizio)
    return query.filter(or_(riferimento.is_(None), riferimento >= oggi))


@app.get("/sagre/vicine", response_model=VicineResponse)
def sagre_vicine(
    lat: Optional[float] = Query(None, description="Latitudine dell'utente (omettila insieme a leng per avere tutti gli eventi)"),
    leng: Optional[float] = Query(None, description="Longitudine dell'utente (omettila insieme a lat per avere tutti gli eventi)"),
    raggio_km: Optional[float] = Query(None, gt=0, description="Raggio di ricerca in km (obbligatorio se lat/leng sono forniti)"),
    limit: Optional[int] = Query(None, gt=0, le=1000, description="Numero massimo di risultati"),
    solo_attive: bool = Query(True, description="Se True (default), esclude le sagre già concluse: mostra solo quelle in corso o future"),
    db: Session = Depends(get_db),
):
    if (lat is None) != (leng is None):
        raise HTTPException(status_code=400, detail="lat e leng vanno forniti insieme, oppure omessi entrambi.")

    oggi = date.today().isoformat()

    if lat is None:
        # data_inizio è in formato ISO "YYYY-MM-DD": l'ordinamento alfabetico
        # coincide con quello cronologico. I valori nulli vanno in fondo
        # (NULL è "più piccolo" in MySQL, quindi finirebbero primi senza questo).
        query = db.query(SagraDB)
        if solo_attive:
            query = _filtro_solo_attive(query, oggi)
        eventi = (
            query
            .order_by(SagraDB.data_inizio.is_(None), SagraDB.data_inizio)
            .limit(limit)
            .all()
        )
        risultati = [_to_evento_con_distanza(ev, None) for ev in eventi]
        return VicineResponse(
            lat=None,
            leng=None,
            raggio_km=None,
            totale_trovati=len(risultati),
            risultati=risultati,
        )

    if raggio_km is None:
        raggio_km = 70

    # lat/leng sono salvate come stringa (vedi db_models.py): un pre-filtro
    # per bounding box a livello SQL (BETWEEN) non è affidabile su una
    # colonna testuale, quindi si recuperano tutti i candidati con
    # coordinate presenti e si calcola la distanza esatta lato applicazione,
    # castando a float (valori non numerici vengono scartati).
    query = db.query(SagraDB).filter(SagraDB.lat.isnot(None), SagraDB.leng.isnot(None))
    if solo_attive:
        query = _filtro_solo_attive(query, oggi)
    candidati = query.all()

    risultati = []
    for ev in candidati:
        try:
            ev_lat, ev_leng = float(ev.lat), float(ev.leng)
        except (TypeError, ValueError):
            continue
        dist = haversine_km(lat, leng, ev_lat, ev_leng)
        if dist <= raggio_km:
            risultati.append(_to_evento_con_distanza(ev, round(dist, 2)))

    # L'ordine è guidato principalmente dalla data (dalla più vicina alla più
    # lontana): una sagra più lontana ma che inizia prima viene mostrata
    # prima di una più vicina che inizia dopo. La distanza è solo il criterio
    # secondario, a parità/vicinanza di data.
    risultati.sort(key=lambda r: ( r.distanza_km))
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
        regione=ev.regione,
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