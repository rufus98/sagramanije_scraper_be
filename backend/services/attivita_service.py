"""
Logica applicativa per le attività collegate a una sagra.
"""
from itertools import groupby

from sqlalchemy.orm import Session

from db_models import AttivitaDB


def lista_attivita_per_sagra(db: Session, id_sagra: int) -> list[AttivitaDB]:
    """Attività di una sagra, ordinate per giorno e ora di inizio (i valori nulli vanno in fondo)."""
    return (
        db.query(AttivitaDB)
        .filter(AttivitaDB.id_sagra == id_sagra)
        .order_by(
            AttivitaDB.giorno.is_(None), AttivitaDB.giorno,
            AttivitaDB.ora_inizio.is_(None), AttivitaDB.ora_inizio,
        )
        .all()
    )


def raggruppa_per_giorno(righe: list[AttivitaDB]) -> list[dict]:
    """
    Raggruppa in [{"giorno": ..., "attivita": [...]}]. Richiede che le righe
    siano già ordinate per giorno (vedi lista_attivita_per_sagra): groupby
    raggruppa solo elementi consecutivi con la stessa chiave.
    """
    return [
        {"giorno": giorno, "attivita": list(gruppo)}
        for giorno, gruppo in groupby(righe, key=lambda r: r.giorno)
    ]


def sagra_ha_attivita(db: Session, id_sagra: int) -> bool:
    return db.query(AttivitaDB.id).filter(AttivitaDB.id_sagra == id_sagra).first() is not None


def crea_attivita(db: Session, id_sagra: int, dati: dict) -> AttivitaDB:
    attivita = AttivitaDB(id_sagra=id_sagra, **dati)
    db.add(attivita)
    db.commit()
    db.refresh(attivita)
    return attivita
