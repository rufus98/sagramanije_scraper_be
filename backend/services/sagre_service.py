"""
Logica applicativa per le sagre: import/upsert nel DB, calcolo prossimità,
filtro sulle sagre attive. Le rotte (routers/sagre.py) restano un layer
sottile che valida l'input e chiama queste funzioni.
"""
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from db_models import SagraDB, make_event_key
from geocoding import enrich_events_with_coordinates
from schemas import SagraEventWithDistance


def importa_eventi(db: Session, events: list[dict]) -> dict:
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


def evento_con_distanza(ev: SagraDB, distanza_km: Optional[float]) -> SagraEventWithDistance:
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


def filtro_solo_attive(query, oggi: str):
    """
    Esclude le sagre già concluse, mantenendo quelle in corso (oggi tra
    data_inizio e data_fine) e quelle che iniziano entro i prossimi ~2 mesi.
    Le date sono stringhe ISO "YYYY-MM-DD", quindi il confronto
    lessicografico coincide con quello cronologico. Un evento senza alcuna
    data nota viene comunque incluso (non si può stabilire che sia passato).
    """
    data_oggi_dt = datetime.strptime(oggi, "%Y-%m-%d")

    # Aggiungiamo circa 61 giorni (equivalenti a 2 mesi) alla data attuale
    due_mesi_futuro_dt = data_oggi_dt + timedelta(days=61)
    due_mesi_futuro = due_mesi_futuro_dt.strftime("%Y-%m-%d")

    # Riferimento per capire se l'evento è già concluso
    riferimento_fine = func.coalesce(SagraDB.data_fine, SagraDB.data_inizio)

    # Riferimento per capire quando l'evento inizia
    riferimento_inizio = func.coalesce(SagraDB.data_inizio, SagraDB.data_fine)

    return query.filter(
        or_(
            and_(SagraDB.data_inizio.is_(None), SagraDB.data_fine.is_(None)),
            and_(
                riferimento_fine >= oggi,
                riferimento_inizio <= due_mesi_futuro,
            ),
        )
    )
