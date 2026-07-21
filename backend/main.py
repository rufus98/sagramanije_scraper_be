"""
API FastAPI per gestione sagre, con persistenza su MySQL.

Struttura:
  - routers/    dichiarazioni delle rotte HTTP (validazione input, status code)
  - services/   logica applicativa effettiva, chiamata dai router
  - db_models.py / schemas.py   modelli SQLAlchemy / Pydantic
  - database.py / geocoding.py / distance.py   moduli di supporto

Rotte principali:
  POST /sagre/ottimizza
      Prende un JSON di eventi, geocodifica solo le città senza lat/leng
      (deduplicate + cache persistente su tabella `geocode_cache`), e fa
      upsert nella tabella `sagre` (nessun duplicato anche se rimandi
      più volte lo stesso evento).

  GET /sagre/vicine
      Data una coordinata (lat, leng) e un raggio in km, interroga il DB
      e restituisce le sagre entro quel raggio, ordinate per data
      (dalla più vicina alla più lontana).

  GET /sagre/{id}
      Dettaglio di una singola sagra, incluso se ha attività collegate.

  GET/POST /sagre/{id}/attivita
      Attività (spettacoli, laboratori, ...) collegate a una sagra.
"""
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import OperationalError

from database import Base, engine
from routers.attivita import router as attivita_router
from routers.sagre import router as sagre_router

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

app.include_router(sagre_router)
app.include_router(attivita_router)


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


@app.get("/")
def root():
    return {
        "message": "Sagre API attiva (MySQL)",
        "endpoints": [
            "/sagre/ottimizza (POST)",
            "/sagre/vicine (GET)",
            "/sagre/{id} (GET)",
            "/sagre/{id}/attivita (GET, POST)",
            "/docs",
        ],
    }
