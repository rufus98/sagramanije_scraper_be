"""
Configurazione della connessione al database MySQL.
Le credenziali arrivano da variabili d'ambiente (vedi docker-compose.yml / .env).
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_USER = os.getenv("MYSQL_USER", "sagre_user")
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "sagre_pass")
DB_HOST = os.getenv("MYSQL_HOST", "localhost")
DB_PORT = os.getenv("MYSQL_PORT", "3316")
DB_NAME = os.getenv("MYSQL_DATABASE", "sagre_db")

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}?charset=utf8mb4"

# pool_pre_ping evita errori con connessioni MySQL "cadute" per timeout di inattività
engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency FastAPI: apre una sessione e la chiude sempre a fine richiesta."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
