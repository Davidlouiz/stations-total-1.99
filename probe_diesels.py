#!/usr/bin/env python3
"""Diagnostic : TotalEnergies référence DEUX diesels distincts par station (codes
`GO` et `GOEX`), tous deux affichés « Diesel Premier » dans son propre outil, et
leurs disponibilités sont **indépendantes** — l'un peut être en rupture sans que
l'autre le soit.

Ce script interroge la fiche station de l'API PoiFinder sur un échantillon de
stations du CSV et affiche l'état des deux codes.

La clé d'API utilisée est publique : elle est servie par le JavaScript du
localisateur TotalEnergies. Elle ressemble à un secret Azure AD, donc GitHub
Push Protection refuse tout commit qui la contient : elle est récupérée au
moment de l'exécution, jamais stockée ici.

Usage
-----
    python3 probe_diesels.py
    TOTAL_API_KEY=... python3 probe_diesels.py     # pour forcer une clé
"""

from __future__ import annotations

import csv
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

API = ("https://apis.poifinder.alzp.tgscloud.net/poi-finder-store-locator-back"
       "/api/v1/point-of-interest")
FRONT_JS = "https://customdevs.woosmap.com/total/front.js"
ENTETES_NAVIGATEUR = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"}
CODES_DIESEL = ("GO", "GOEX")


def cle_api() -> str:
    """Clé publique du localisateur (jamais stockée dans le dépôt)."""
    if os.environ.get("TOTAL_API_KEY"):
        return os.environ["TOTAL_API_KEY"]
    with urllib.request.urlopen(
            urllib.request.Request(FRONT_JS, headers=ENTETES_NAVIGATEUR), timeout=30) as reponse:
        javascript = reponse.read().decode("utf-8", "replace")
    trouve = re.search(r'totalKey:"([^"]+)"', javascript)
    if not trouve:
        raise SystemExit("Clé d'API introuvable dans le JavaScript du localisateur.")
    return trouve.group(1)


def fiche_station(location_id: str, cle: str) -> dict:
    url = f"{API}?location_id={location_id}&type=FUELING"
    entetes = dict(ENTETES_NAVIGATEUR, **{"API-Key": cle})
    with urllib.request.urlopen(urllib.request.Request(url, headers=entetes), timeout=30) as reponse:
        return json.load(reponse)


def main() -> int:
    cle = cle_api()
    lignes = list(csv.DictReader(
        open(Path(__file__).parent / "data" / "stations_france.csv", encoding="utf-8-sig"),
        delimiter=";"))

    # Échantillon : des stations de réseau, des relais et des stations Access
    echantillon = ([r for r in lignes if r["Nom"].startswith("LAGARDE")][:12]
                   + [r for r in lignes if r["Nom"].startswith("RELAIS")][:8]
                   + [r for r in lignes if r["Enseigne"].startswith("TotalEnergies")][:8])

    print(f"{'station':<30} {'ville':<22} {'GO':<12} {'GOEX':<12}")
    combinaisons: dict[tuple[str, str], int] = {}
    for ligne in echantillon:
        try:
            fiche = fiche_station(ligne["ID station"], cle)
        except urllib.error.HTTPError as erreur:
            print(f"{ligne['ID station']:<30} erreur {erreur.code}")
            continue
        etats = {p["product_code"]: p.get("availability_status")
                 for p in fiche.get("products_and_services") or []
                 if p.get("product_code") in CODES_DIESEL}
        if not etats:
            continue
        couple = (str(etats.get("GO")), str(etats.get("GOEX")))
        combinaisons[couple] = combinaisons.get(couple, 0) + 1
        print(f"{ligne['ID station'] + ' ' + ligne['Nom'][:14]:<30} {ligne['Ville'][:20]:<22} "
              f"{couple[0]:<12} {couple[1]:<12}")

    print("\nCombinaisons observées (GO, GOEX) :")
    for couple, nombre in sorted(combinaisons.items(), key=lambda item: -item[1]):
        print(f"   {nombre:>3} × {couple}")
    print("\nDeux états différents dans un même échantillon ⇒ les deux diesels sont")
    print("des produits distincts, avec leurs propres cuves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
