#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
monificadati.py
================
Bonifica trovasagre2.0.json (l'output unito di main_scraper.py): rimuove i
duplicati confrontando nome_sagra, data_inizio, data_fine, citta, provincia
e regione, e segnala eventuali anomalie nei dati.

Il confronto è tollerante a differenze non semantiche tra le due fonti
(trovasagre.com e sagr.it), la causa più comune di duplicati mancati:
  - citta con un suffisso tra parentesi ridondante, es. "Collecorvino (abruzzo)"
    vs "Collecorvino" -> le parentesi vengono rimosse prima del confronto
    (e nell'output finale).
  - maiuscole/minuscole e spazi superflui in nome_sagra/citta/provincia/regione.
  - regione scritta in modo diverso tra le due fonti: "Friuli Venezia Giulia"
    vs "Friuli-Venezia Giulia" (trattino), oppure nomi bilingui restituiti da
    Nominatim tipo "Sardigna/Sardegna" o "Trentino-Alto Adige/Südtirol" ->
    vengono trattati come alias dello stesso nome.

Quando trova un gruppo di duplicati li unisce in un unico record, tenendo il
valore migliore per ogni campo non usato come chiave (descrizione più lunga,
locandina/link/ora_inizio non nulli, coordinate numeriche native preferite a
quelle geocodificate e salvate come stringa).

Segnala inoltre (senza correggerle automaticamente, serve una verifica
manuale) le combinazioni citta+provincia associate a più di una regione
*realmente* diversa (non solo un alias): di solito indicano un errore nei
dati sorgente o nel geocoding.

Uso:
    python monificadati.py
    python monificadati.py --input output/trovasagre2.0.json --output output/trovasagre2.0.bonificato.json
    python monificadati.py --in-place   # sovrascrive il file di input
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

DEFAULT_INPUT = "output/trovasagre2.0.json"


def pulisci_citta(citta: str | None) -> str | None:
    """Rimuove un suffisso tra parentesi ridondante, es. "Collecorvino (abruzzo)" -> "Collecorvino"."""
    if not citta:
        return citta
    pulita = re.sub(r"\s*\([^)]*\)\s*$", "", citta).strip()
    return pulita or citta.strip()


def normalizza_testo(valore) -> str:
    return re.sub(r"\s+", " ", str(valore or "")).strip().casefold()


def varianti_regione(regione: str | None) -> set[str]:
    """
    Restituisce l'insieme degli "alias" normalizzati di una regione, per
    riconoscere come equivalenti forme diverse della stessa regione:
    trattino/spazio ("Friuli-Venezia Giulia" / "Friuli Venezia Giulia") e
    nomi bilingui restituiti da Nominatim ("Sardigna/Sardegna",
    "Trentino-Alto Adige/Südtirol", "Valle d'Aosta / Vallée d'Aoste").
    """
    if not regione:
        return set()
    base = re.sub(r"\s+", " ", str(regione)).strip()
    parti = {base, *re.split(r"\s*/\s*", base)}
    varianti = set()
    for parte in parti:
        if parte:
            varianti.add(normalizza_testo(parte))
            varianti.add(normalizza_testo(parte.replace("-", " ")))
    return varianti


def regioni_equivalenti(a: str | None, b: str | None) -> bool:
    va, vb = varianti_regione(a), varianti_regione(b)
    if not va or not vb:
        return True  # una delle due non ha regione nota: non blocca il confronto sugli altri campi
    return bool(va & vb)


def chiave_base(ev: dict) -> tuple:
    """Le chiavi diverse dalla regione (gestita a parte, vedi regioni_equivalenti)."""
    return (
        normalizza_testo(ev.get("nome_sagra")),
        normalizza_testo(ev.get("data_inizio")),
        normalizza_testo(ev.get("data_fine")),
        normalizza_testo(pulisci_citta(ev.get("citta"))),
        normalizza_testo(ev.get("provincia")),
    )


def migliore_valore(a, b, preferisci_lungo: bool = False):
    if a in (None, "") and b not in (None, ""):
        return b
    if b in (None, "") and a not in (None, ""):
        return a
    if a not in (None, "") and b not in (None, "") and preferisci_lungo:
        return a if len(str(a)) >= len(str(b)) else b
    return a if a not in (None, "") else b


def migliore_coordinata(a, b):
    """
    Tra due coordinate preferisce il valore numerico nativo (dall'API di
    trovasagre.com) a quello geocodificato via Nominatim, spesso salvato
    come stringa e meno affidabile del dato originale della fonte.
    """
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(a, float) and not isinstance(b, float):
        return a
    if isinstance(b, float) and not isinstance(a, float):
        return b
    return a


def unisci_record(esistente: dict, nuovo: dict) -> dict:
    unito = dict(esistente)
    unito["citta"] = migliore_valore(esistente.get("citta"), nuovo.get("citta"))
    unito["regione"] = migliore_valore(esistente.get("regione"), nuovo.get("regione"))
    unito["locandina"] = migliore_valore(esistente.get("locandina"), nuovo.get("locandina"))
    unito["link_pagina_ufficiale"] = migliore_valore(esistente.get("link_pagina_ufficiale"), nuovo.get("link_pagina_ufficiale"))
    unito["descrizione"] = migliore_valore(esistente.get("descrizione"), nuovo.get("descrizione"), preferisci_lungo=True)
    unito["ora_inizio"] = migliore_valore(esistente.get("ora_inizio"), nuovo.get("ora_inizio"))
    unito["category"] = migliore_valore(esistente.get("category"), nuovo.get("category"))
    unito["lat"] = migliore_coordinata(esistente.get("lat"), nuovo.get("lat"))
    unito["leng"] = migliore_coordinata(esistente.get("leng"), nuovo.get("leng"))
    return unito


def bonifica(eventi: list[dict]) -> dict:
    quasi_gruppi: dict[tuple, list[dict]] = {}
    ordine_quasi: list[tuple] = []
    citta_corrette: dict[str, str] = {}

    for ev in eventi:
        citta_originale = ev.get("citta")
        citta_pulita = pulisci_citta(citta_originale)
        if citta_originale and citta_pulita != citta_originale:
            citta_corrette[citta_originale] = citta_pulita

        record = dict(ev)
        record["citta"] = citta_pulita

        qk = chiave_base(record)
        if qk not in quasi_gruppi:
            quasi_gruppi[qk] = []
            ordine_quasi.append(qk)
        quasi_gruppi[qk].append(record)

    eventi_bonificati = []
    duplicati_rimossi = 0
    anomalie_regione = []

    for qk in ordine_quasi:
        # Dentro ogni quasi-gruppo (stessi nome_sagra/date/citta/provincia),
        # unisce solo i record la cui regione è equivalente (vedi
        # regioni_equivalenti); regioni davvero diverse restano separate e
        # vengono segnalate come possibile anomalia.
        sottogruppi: list[dict] = []
        for record in quasi_gruppi[qk]:
            aggiunto = False
            for sotto in sottogruppi:
                if regioni_equivalenti(sotto["record"].get("regione"), record.get("regione")):
                    sotto["record"] = unisci_record(sotto["record"], record)
                    sotto["regioni_viste"].add(record.get("regione"))
                    duplicati_rimossi += 1
                    aggiunto = True
                    break
            if not aggiunto:
                sottogruppi.append({"record": record, "regioni_viste": {record.get("regione")}})

        if len(sottogruppi) > 1:
            regioni_diverse = sorted({r for s in sottogruppi for r in s["regioni_viste"] if r})
            if len(regioni_diverse) > 1:
                nome_sagra, data_inizio, data_fine, citta, provincia = qk
                anomalie_regione.append({
                    "nome_sagra": sottogruppi[0]["record"].get("nome_sagra"),
                    "citta": sottogruppi[0]["record"].get("citta"),
                    "provincia": provincia,
                    "data_inizio": data_inizio,
                    "regioni_trovate": regioni_diverse,
                })

        eventi_bonificati.extend(s["record"] for s in sottogruppi)

    return {
        "eventi": eventi_bonificati,
        "stats": {
            "totale_input": len(eventi),
            "totale_output": len(eventi_bonificati),
            "duplicati_rimossi": duplicati_rimossi,
            "citta_corrette": citta_corrette,
        },
        "anomalie_regione": anomalie_regione,
    }


def main():
    parser = argparse.ArgumentParser(description="Bonifica duplicati e anomalie in trovasagre2.0.json")
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"File JSON da bonificare (default: {DEFAULT_INPUT})")
    parser.add_argument("--output", default=None, help="Default: <input>.bonificato.json")
    parser.add_argument("--in-place", action="store_true", help="Sovrascrive il file di input invece di crearne uno nuovo")
    parser.add_argument("--report", default=None, help="Percorso del report JSON (default: <output>.report.json)")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = input_path if args.in_place else Path(args.output) if args.output else input_path.with_name(input_path.stem + ".bonificato.json")
    report_path = Path(args.report) if args.report else output_path.with_name(output_path.stem + ".report.json")

    with input_path.open("r", encoding="utf-8") as f:
        eventi = json.load(f)

    esito = bonifica(eventi)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(esito["eventi"], f, ensure_ascii=False, indent=2)

    report = {**esito["stats"], "anomalie_regioni_diverse_stessa_citta": esito["anomalie_regione"]}
    with report_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    stats = esito["stats"]
    print(f"Eventi in input: {stats['totale_input']}")
    print(f"Duplicati rimossi: {stats['duplicati_rimossi']}")
    print(f"Eventi in output: {stats['totale_output']}")
    print(f"Citta corrette (parentesi rimosse): {len(stats['citta_corrette'])}")
    for originale, pulita in list(stats["citta_corrette"].items())[:20]:
        print(f"  {originale!r} -> {pulita!r}")
    if len(stats["citta_corrette"]) > 20:
        print(f"  ... e altre {len(stats['citta_corrette']) - 20} (vedi report)")
    if esito["anomalie_regione"]:
        print(f"Attenzione: {len(esito['anomalie_regione'])} eventi con la stessa citta+provincia ma regioni diverse (vedi report, non corretti automaticamente)")
    print(f"Salvato: {output_path}")
    print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
