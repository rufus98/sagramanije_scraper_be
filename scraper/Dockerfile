# Dockerfile per lo scraper sagre italiane
FROM python:3.11-slim

# Evita output bufferizzato: i print dello script arrivano subito nei log
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install lxml
run pip install pandas

COPY main_scraper.py .
COPY scraper.py .
#COPY scraper_sagreautentiche.py .
COPY scraper_sagre.py .



COPY sagreautentiche.json .
# L'output viene scritto qui dentro; nel docker-compose questa cartella
# e' montata come volume verso l'host, cosi' il file resta anche dopo
# che il container si ferma.
RUN mkdir -p /app/output

#ENTRYPOINT ["python3", "scraper_sagre_json.py"]
#CMD ["--output", "/app/output/sagre_italia.json", "--max-eventi", "1", "--dettagli" ,"--solo-fonte", "trovasagre"]
ENTRYPOINT ["python3", "main_scraper.py"]
#ENTRYPOINT ["python3", "scraper_sagreautentiche.py"]
#ENTRYPOINT ["python3", "scraper_sagre.py"]

CMD ["--output", "/app/output/trovasagre.json"]
#CMD ["--output", "/app/output/sagreautentiche_completo.json"]
#CMD ["--output", "/app/output/sagreautentiche_data_incerta.json"]
#CMD ["--output", "/app/output/sagre_completo"]
