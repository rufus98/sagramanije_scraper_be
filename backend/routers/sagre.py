"""
Rotte HTTP per le sagre. Layer sottile: valida input/query params e delega
la logica a services/sagre_service.py e services/attivita_service.py.
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, case, cast, func
from sqlalchemy.orm import Session

from database import get_db
from db_models import SagraDB
from distance import haversine_km
from schemas import OttimizzaRequest, OttimizzaResponse, SagraEvent, VicineResponse
from services import attivita_service, sagre_service

router = APIRouter(prefix="/sagre", tags=["sagre"])


# NOTA: rotta attualmente disabilitata (manca il decoratore @router.post) —
# così era anche prima della riorganizzazione, comportamento preservato.
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

    result = sagre_service.importa_eventi(db, events)

    return OttimizzaResponse(stats=result["stats"], events=result["events"])


@router.get("/vicine", response_model=VicineResponse)
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

    #se non vengono presi ne latitudine e ne longitudine
    if lat is None and leng is None:
        query = db.query(SagraDB)
        if solo_attive:
            query = sagre_service.filtro_solo_attive(query, oggi)
        giorni_a_inizio = case(
                (func.now() < SagraDB.data_inizio, func.datediff(SagraDB.data_inizio, func.now())),
                else_=0
            ).label("giorni_mancanti")
        query = query.add_columns(giorni_a_inizio)
        query_all = query.filter(SagraDB.regione == "Abruzzo")
        eventi = query_all.all()

        risultati = [sagre_service.evento_con_distanza(ev, None, giorni_a_inizio) for ev,giorni_a_inizio in eventi]
        return VicineResponse(
            lat=None,
            leng=None,
            raggio_km=None,
            totale_trovati=len(risultati),
            risultati=risultati,
        )
    #processo standard
    
    #    raggio_km = 400

    query = db.query(SagraDB).filter(SagraDB.lat.isnot(None), SagraDB.leng.isnot(None))

    if solo_attive:
        query = sagre_service.filtro_solo_attive(query, oggi)

    giorni_a_inizio = case(
            (func.now() < SagraDB.data_inizio, func.datediff(SagraDB.data_inizio, func.now())),
            else_=0
        ).label("giorni_mancanti")
    query = query.add_columns(giorni_a_inizio)
    
    query_all = query.filter(SagraDB.regione == "Abruzzo")
    candidati = query_all.all()
    risultati = []
    for ev,giorni_a_inizio in candidati:
        try:
            ev_lat, ev_leng = float(ev.lat), float(ev.leng)
        except (TypeError, ValueError):
            continue
        dist = haversine_km(lat, leng, ev_lat, ev_leng)
        if raggio_km is not None:
            if dist <= raggio_km:
                risultati.append(sagre_service.evento_con_distanza(ev, round(dist, 2),giorni_a_inizio))
        else: 
            risultati.append(sagre_service.evento_con_distanza(ev, round(dist, 2),giorni_a_inizio))
    risultati.sort(key=lambda r: (r.giorni, r.distanza_km))
    risultati = risultati[:limit]

    return VicineResponse(
        lat=lat,
        leng=leng,
        raggio_km=raggio_km,
        totale_trovati=len(risultati),
        risultati=risultati,
    )


@router.get("/{sagra_id}", response_model=SagraEvent)
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
        ha_attivita=attivita_service.sagra_ha_attivita(db, sagra_id),
    )
