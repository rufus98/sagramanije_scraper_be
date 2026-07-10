#!/usr/bin/env python3
"""
Scraper completo per sagreautentiche.it
========================================

Estrae TUTTI gli eventi (sagre, feste, fiere) presenti sul sito
https://sagreautentiche.it e li salva in un file JSON, con lo
stesso schema di output usato per sagr.it:

    {
        "nome_sagra": ...,
        "data_inizio": ...,
        "data_fine": ...,
        "citta": ...,
        "provincia": ...,
        "lat": ...,
        "leng": ...,
        "locandina": ...,
        "link_pagina_ufficiale": ...,
        "category": "sagra"
    }

STRUTTURA DEL SITO (osservata)
-------------------------------
- Homepage:            https://sagreautentiche.it/
- Pagine regione:      https://sagreautentiche.it/region/{regione}/
                       (con paginazione tipo .../region/{regione}/page/2/)
- Pagine singolo evento: https://sagreautentiche.it/sagre/{slug-evento}/
- Pagine blog (NON eventi): https://sagreautentiche.it/blog/...
  -> vanno escluse dallo scraping degli eventi.

STRATEGIA
---------
1. Prova prima /sitemap.xml (o sitemap-index.xml, tipico di WordPress
   con Yoast/RankMath: sitemap_index.xml + sotto-sitemap tipo
   sagre-sitemap.xml) per raccogliere direttamente tutti gli URL
   /sagre/ del sito: metodo più rapido e completo.
2. In fallback, crawling manuale:
      homepage -> pagine /region/{regione}/ (con paginazione) ->
      link alle singole pagine /sagre/{slug}/
3. Per ogni pagina evento estrae i dati via JSON-LD (schema.org/Event,
   spesso presente nei siti WordPress con plugin SEO/eventi) con
   fallback su selettori HTML generici.
4. Salva incrementalmente in sagreautentiche_completo.json.

REQUISITI
---------
    pip install requests beautifulsoup4 lxml

USO
---
    python scrape_sagreautentiche_it.py

NOTE
----
- Rispetta https://sagreautentiche.it/robots.txt e il DELAY_SECONDS
  impostato qui sotto.
- Se lo scraping smette di funzionare o i campi risultano vuoti,
  ispeziona l'HTML aggiornato di una pagina /sagre/... e adegua i
  selettori nelle funzioni parse_*.
"""

import json
import re
import time
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# CONFIGURAZIONE
# ----------------------------------------------------------------------

BASE_URL = "https://sagreautentiche.it"
OUTPUT_FILE = Path(__file__).parent / "sagreautentiche_completo.json"
UNCERTAIN_FILE = Path(__file__).parent / "sagreautentiche_data_incerta.json"
DELAY_SECONDS = 1.0
TIMEOUT = 20
MAX_RETRIES = 3
SAVE_EVERY = 50

# ---- FILTRI OUTPUT --------------------------------------------------
FILTER_YEAR = 2026           # tiene solo eventi che ricadono in quest'anno
                             # (None per disattivare il filtro anno)
FILTER_ONLY_CATEGORY = "sagra"  # tiene solo category == questo valore
                                 # (case-insensitive; None per disattivare)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; SagreScraper/1.0; "
        "+per uso personale non commerciale)"
    )
}

REGIONI = [
    "abruzzo", "basilicata", "calabria", "campania", "emilia-romagna",
    "friuli-venezia-giulia", "lazio", "liguria", "lombardia", "marche",
    "molise", "piemonte", "puglia", "sardegna", "sicilia", "toscana",
    "trentino-alto-adige", "umbria", "valle-d-aosta", "veneto",
]

session = requests.Session()
session.headers.update(HEADERS)


def make_soup(html):
    """
    Crea un oggetto BeautifulSoup usando 'lxml' se disponibile,
    altrimenti ricade sul parser 'html.parser' incluso di serie in
    Python (nessuna dipendenza esterna richiesta). Evita il crash
    bs4.FeatureNotFound quando lxml non è installato correttamente.
    """
    try:
        return BeautifulSoup(html, "lxml")
    except Exception:
        return BeautifulSoup(html, "html.parser")


# ----------------------------------------------------------------------
# UTILITY DI RETE
# ----------------------------------------------------------------------

def fetch(url, as_xml=False):
    """Scarica una URL con retry ed exponential backoff."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT)
            if resp.status_code == 200:
                time.sleep(DELAY_SECONDS)
                return resp.content if as_xml else resp.text
            elif resp.status_code == 404:
                return None
            else:
                print(f"  [WARN] {url} -> HTTP {resp.status_code}")
        except requests.RequestException as e:
            print(f"  [ERR] tentativo {attempt}/{MAX_RETRIES} su {url}: {e}")
        time.sleep(DELAY_SECONDS * attempt)
    return None


def is_event_url(url):
    """Le pagine evento su sagreautentiche.it sono sotto /sagre/<slug>/."""
    path = urlparse(url).path.rstrip("/")
    return path.startswith("/sagre/") and path != "/sagre"


# ----------------------------------------------------------------------
# STRATEGIA 1: SITEMAP
# ----------------------------------------------------------------------

def get_sitemap_urls():
    """
    Cerca sitemap.xml / sitemap_index.xml (comuni nei siti WordPress,
    es. generate da Yoast SEO o RankMath) e ne estrae gli URL /sagre/.
    """
    candidates = [
        f"{BASE_URL}/sitemap.xml",
        f"{BASE_URL}/sitemap_index.xml",
        f"{BASE_URL}/sitemap-index.xml",
        f"{BASE_URL}/sagre-sitemap.xml",
        f"{BASE_URL}/sagre-sitemap1.xml",
    ]

    event_urls = set()
    sub_sitemaps = []

    for cand in candidates:
        content = fetch(cand, as_xml=True)
        if not content:
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            continue

        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for sm in root.findall(".//sm:sitemap/sm:loc", ns):
            sub_sitemaps.append(sm.text.strip())
        for loc in root.findall(".//sm:url/sm:loc", ns):
            u = loc.text.strip()
            if is_event_url(u):
                event_urls.add(u)

    # esplora le sotto-sitemap, dando priorità a quelle che sembrano
    # riguardare gli eventi/sagre (nome file contenente "sagre" o "post")
    sub_sitemaps_sorted = sorted(
        sub_sitemaps, key=lambda u: ("sagre" not in u.lower(), u)
    )
    for sm_url in sub_sitemaps_sorted:
        content = fetch(sm_url, as_xml=True)
        if not content:
            continue
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            continue
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        for loc in root.findall(".//sm:url/sm:loc", ns):
            u = loc.text.strip()
            if is_event_url(u):
                event_urls.add(u)

    return sorted(event_urls)


# ----------------------------------------------------------------------
# STRATEGIA 2: CRAWLING MANUALE (fallback)
# ----------------------------------------------------------------------

def discover_event_links(html, page_url):
    """Estrae tutti i link a pagine /sagre/<slug>/ da una pagina qualsiasi."""
    soup = make_soup(html)
    links = set()
    for a in soup.select("a[href]"):
        full = urljoin(page_url, a["href"]).split("?")[0].split("#")[0]
        if full.startswith(BASE_URL) and is_event_url(full):
            links.add(full)
    return links


def discover_pagination_links(html, page_url):
    """Cerca link di paginazione tipo /region/{regione}/page/2/."""
    soup = make_soup(html)
    links = set()
    for a in soup.select("a[href]"):
        full = urljoin(page_url, a["href"])
        if full.startswith(BASE_URL) and re.search(r"/page/\d+/?$", full):
            links.add(full)
    return links


def crawl_manual():
    """Crawling manuale: pagine /region/{regione}/ (con paginazione) -> eventi."""
    all_event_urls = set()
    visited_listing_pages = set()

    to_visit = [f"{BASE_URL}/region/{r}/" for r in REGIONI]
    # aggiunge anche l'homepage come ulteriore punto di ingresso
    to_visit.append(f"{BASE_URL}/")

    while to_visit:
        url = to_visit.pop()
        if url in visited_listing_pages:
            continue
        visited_listing_pages.add(url)

        print(f"[crawl] {url}")
        html = fetch(url)
        if not html:
            continue

        found = discover_event_links(html, url)
        if found:
            print(f"    trovati {len(found)} eventi")
        all_event_urls |= found

        for pag_url in discover_pagination_links(html, url):
            if pag_url not in visited_listing_pages:
                to_visit.append(pag_url)

    return sorted(all_event_urls)


# ----------------------------------------------------------------------
# PARSING PAGINA EVENTO
# ----------------------------------------------------------------------

MESI_ITALIANI = {
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
}


def extract_category(soup, jsonld_data=None):
    """
    Estrae la categoria/tipologia dell'evento così come scritta sul
    sito, invece di usare un valore fisso.

    sagreautentiche.it è basato su WordPress: la categoria è quasi
    sempre esposta come link con rel="category tag" (pattern standard
    dei temi WP) vicino al titolo dell'articolo/evento. Strategia:
      1. Link <a rel="category tag"> o <a rel="category">.
      2. Meta tag "article:section" (comune con plugin SEO come Yoast).
      3. JSON-LD (campo "articleSection", "genre", "category").
      4. Badge testuale breve subito sopra l'h1 (come fallback generico).
      5. Fallback finale: "Sagra".
    """
    # 1. Link WordPress standard di categoria
    cat_link = soup.select_one('a[rel~="category"]')
    if cat_link:
        txt = cat_link.get_text(strip=True)
        if txt:
            return txt

    # 2. Meta tag article:section (Yoast/RankMath)
    meta_section = soup.select_one('meta[property="article:section"]')
    if meta_section and meta_section.get("content"):
        return meta_section["content"].strip()

    # 3. JSON-LD
    if jsonld_data:
        for key in ("articleSection", "genre", "category", "eventCategory"):
            val = jsonld_data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
            if isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, str) and first.strip():
                    return first.strip()

    # 4. Badge testuale breve subito sopra l'h1 (fallback generico,
    #    utile se il tema non usa i tag WP standard)
    h1 = soup.find("h1")
    if h1:
        candidates = []
        parent = h1.parent
        depth = 0
        while parent and depth < 4:
            for sibling in parent.find_all(recursive=False):
                if sibling is h1:
                    break
                if sibling.name == "a":
                    continue
                txt = sibling.get_text(strip=True)
                if txt and 1 <= len(txt.split()) <= 4 and len(txt) <= 30:
                    candidates.append(txt)
            depth += 1
            parent = parent.parent

        for txt in candidates:
            if txt.lower() in MESI_ITALIANI:
                continue
            if any(mese in txt.lower() for mese in MESI_ITALIANI) and len(txt) <= 12:
                continue
            return txt

    # 5. Fallback finale
    return "Sagra"


def extract_year(*date_strings):
    """Estrae il primo anno a 4 cifre trovato in una o più stringhe data."""
    for s in date_strings:
        if not s:
            continue
        m = re.search(r"\b(20\d{2})\b", str(s))
        if m:
            return int(m.group(1))
    return None


def filter_events(events):
    """
    Applica i filtri FILTER_YEAR e FILTER_ONLY_CATEGORY.

    Ritorna (eventi_validi, eventi_data_incerta):
    - eventi_validi: eventi che rispettano i filtri richiesti.
    - eventi_data_incerta: eventi con categoria OK ma senza un anno
      riconoscibile in data_inizio/data_fine; esclusi dall'output
      principale ma salvati a parte per controllo manuale invece di
      essere scartati senza possibilità di verifica.
    """
    validi = []
    incerti = []

    for ev in events:
        if FILTER_ONLY_CATEGORY is not None:
            cat = (ev.get("category") or "").strip().lower()
            if cat != FILTER_ONLY_CATEGORY.strip().lower():
                continue

        if FILTER_YEAR is None:
            validi.append(ev)
            continue

        anno = extract_year(ev.get("data_inizio"), ev.get("data_fine"))
        if anno == FILTER_YEAR:
            validi.append(ev)
        elif anno is None:
            incerti.append(ev)
        # anno diverso da FILTER_YEAR -> scartato

    return validi, incerti


def parse_event_page(html, url):
    """
    Estrae i dati da una pagina /sagre/<slug>/ nel formato:

        {
            "nome_sagra": ...,
            "data_inizio": ...,
            "data_fine": ...,
            "citta": ...,
            "provincia": ...,
            "lat": ...,
            "leng": ...,
            "locandina": ...,
            "link_pagina_ufficiale": ...,
            "category": "sagra"
        }
    """
    soup = make_soup(html)

    def text_or_none(sel):
        el = soup.select_one(sel)
        return el.get_text(strip=True) if el else None

    def meta_content(prop):
        el = soup.select_one(f'meta[property="{prop}"]') or soup.select_one(f'meta[name="{prop}"]')
        return el.get("content").strip() if el and el.get("content") else None

    # ---- Nome sagra --------------------------------------------------
    nome_sagra = (
        text_or_none("h1")
        or meta_content("og:title")
        or text_or_none("title")
    )

    # ---- JSON-LD (schema.org/Event) — fonte più affidabile ------------
    jsonld_data = {}
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.string or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = data if isinstance(data, list) else [data]
        # alcuni plugin WP annidano in "@graph"
        expanded = []
        for c in candidates:
            if isinstance(c, dict) and "@graph" in c:
                expanded.extend(c["@graph"])
            else:
                expanded.append(c)
        for c in expanded:
            if isinstance(c, dict) and c.get("@type") in ("Event", "Festival"):
                jsonld_data = c
                break
        if jsonld_data:
            break

    # ---- Date -----------------------------------------------------
    data_inizio = jsonld_data.get("startDate")
    data_fine = jsonld_data.get("endDate")
    if not data_inizio:
        data_inizio = text_or_none("[class*=date]") or text_or_none("time")
        if not data_fine:
            # a volte le date sono in un unico blocco "dal ... al ..."
            full_text = soup.get_text(" ", strip=True)
            m = re.search(
                r"dal\s+(\d{1,2}\s+\w+(?:\s+\d{4})?)\s+al\s+(\d{1,2}\s+\w+\s+\d{4})",
                full_text, re.I,
            )
            if m:
                data_inizio = data_inizio or m.group(1)
                data_fine = m.group(2)

    # ---- Location: citta / provincia / lat / leng ---------------------
    citta = None
    provincia = None
    lat = None
    leng = None

    location = jsonld_data.get("location", {})
    if isinstance(location, dict):
        addr = location.get("address", {})
        if isinstance(addr, dict):
            citta = addr.get("addressLocality")
            provincia = addr.get("addressRegion") or addr.get("addressProvince")
        geo = location.get("geo", {})
        if isinstance(geo, dict):
            lat = geo.get("latitude")
            leng = geo.get("longitude")

    if not citta:
        citta = text_or_none("[class*=location]") or text_or_none("[class*=comune]")

    if not provincia:
        prov_match = re.search(r"\(([A-Z]{2})\)", soup.get_text())
        if prov_match:
            provincia = prov_match.group(1)

    if lat is None or leng is None:
        lat = lat or meta_content("place:location:latitude") or meta_content("geo.position")
        leng = leng or meta_content("place:location:longitude")

    # ---- Locandina (immagine principale/poster) ------------------------
    locandina = (
        jsonld_data.get("image")
        if isinstance(jsonld_data.get("image"), str)
        else None
    )
    if not locandina and isinstance(jsonld_data.get("image"), dict):
        locandina = jsonld_data["image"].get("url")
    if not locandina and isinstance(jsonld_data.get("image"), list) and jsonld_data["image"]:
        first = jsonld_data["image"][0]
        locandina = first if isinstance(first, str) else first.get("url")
    if not locandina:
        locandina = meta_content("og:image")
    if not locandina:
        img = soup.select_one("article img, [class*=featured] img, .wp-post-image")
        if img and img.get("src"):
            locandina = urljoin(url, img["src"])

    # ---- Link pagina ufficiale (esterno, se presente) ------------------
    link_ufficiale = None
    for a in soup.select("a[href]"):
        href = a["href"]
        label = a.get_text(strip=True).lower()
        if any(k in label for k in ("sito ufficiale", "pagina ufficiale", "official", "pro loco")):
            candidate = urljoin(url, href)
            if not candidate.startswith(BASE_URL):
                link_ufficiale = candidate
                break
    if not link_ufficiale:
        link_ufficiale = url

    category = extract_category(soup, jsonld_data)

    return {
        "nome_sagra": nome_sagra,
        "data_inizio": data_inizio,
        "data_fine": data_fine,
        "citta": citta,
        "provincia": provincia,
        "lat": lat,
        "leng": leng,
        "locandina": locandina,
        "link_pagina_ufficiale": link_ufficiale,
        "category": category,
    }


# ----------------------------------------------------------------------
# SALVATAGGIO INCREMENTALE
# ----------------------------------------------------------------------

def save_results(events):
    """Salva la lista di eventi come array JSON puro."""
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(events, f, ensure_ascii=False, indent=2)
    print(f"  -> salvati {len(events)} eventi in {OUTPUT_FILE}")


# ----------------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------------

RAW_OUTPUT_FILE = Path(__file__).parent / "sagreautentiche_raw_non_filtrato.json"


def main():
    print("== STEP 1: ricerca sitemap.xml ==")
    event_urls = get_sitemap_urls()

    if event_urls:
        print(f"Trovati {len(event_urls)} URL evento via sitemap.")
    else:
        print("Nessuna sitemap utilizzabile trovata. Passo al crawling manuale.")
        print("== STEP 1-bis: crawling manuale ==")
        event_urls = crawl_manual()
        print(f"Trovati {len(event_urls)} URL evento via crawling manuale.")

    if not event_urls:
        print("Nessun evento trovato. Verifica la struttura del sito o la connettività.")
        sys.exit(1)

    print(f"\n== STEP 2: scraping di {len(event_urls)} pagine evento ==")
    events = []
    for i, url in enumerate(event_urls, 1):
        print(f"[{i}/{len(event_urls)}] {url}")
        html = fetch(url)
        if not html:
            print("  [skip] pagina non raggiungibile")
            continue
        event_data = parse_event_page(html, url)
        events.append(event_data)

        if i % SAVE_EVERY == 0:
            with open(RAW_OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(events, f, ensure_ascii=False, indent=2)
            print(f"  -> salvataggio incrementale grezzo: {len(events)} eventi")

    print(f"\n== STEP 3: applico i filtri "
          f"(anno={FILTER_YEAR}, category={FILTER_ONLY_CATEGORY!r}) ==")
    validi, incerti = filter_events(events)

    save_results(validi)
    if incerti:
        with open(UNCERTAIN_FILE, "w", encoding="utf-8") as f:
            json.dump(incerti, f, ensure_ascii=False, indent=2)
        print(f"  -> {len(incerti)} eventi con anno non riconoscibile "
              f"salvati in {UNCERTAIN_FILE} per controllo manuale")

    print(f"\nCompletato.")
    print(f"  Eventi totali estratti (grezzo): {len(events)}")
    print(f"  Eventi validi dopo i filtri:      {len(validi)}")
    print(f"  Eventi con data incerta:          {len(incerti)}")
    print(f"File finale: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()