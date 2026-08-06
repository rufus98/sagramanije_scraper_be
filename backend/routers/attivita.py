"""
Rotte HTTP per le attività collegate a una sagra.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from db_models import SagraDB
from schemas import Attivita, AttivitaCreate, AttivitaGiorno, AttivitaListResponse
from services import attivita_service

router = APIRouter(prefix="/sagre", tags=["attivita"])


def _controlla_sagra_esiste(db: Session, sagra_id: int) -> None:
    if db.get(SagraDB, sagra_id) is None:
        raise HTTPException(status_code=404, detail=f"Nessuna sagra trovata con id={sagra_id}.")


@router.get("/{sagra_id}/attivita", response_model=AttivitaListResponse)
def lista_attivita(sagra_id: int, db: Session = Depends(get_db)):
    """Restituisce tutte le attività collegate alla sagra indicata, raggruppate per giorno."""
    _controlla_sagra_esiste(db, sagra_id)

    righe = attivita_service.lista_attivita_per_sagra(db, sagra_id)
    gruppi = attivita_service.raggruppa_per_giorno(righe)
    return AttivitaListResponse(
        id_sagra=sagra_id,
        totale=len(righe),
        giorni=[
            AttivitaGiorno(
                giorno=gruppo["giorno"],
                attivita=[Attivita.model_validate(r, from_attributes=True) for r in gruppo["attivita"]],
            )
            for gruppo in gruppi
        ],
    )
