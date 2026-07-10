FROM python:3.12-slim

WORKDIR /app

# Dipendenze di sistema richieste da mysqlclient/pymysql per compilare eventuali wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-libmysqlclient-dev \
    build-essential \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8010

# --reload utile in sviluppo; per produzione rimuovi il flag e valuta più worker
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8010", "--reload"]
