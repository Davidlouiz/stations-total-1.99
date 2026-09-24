#!/usr/bin/env python3
"""Export compact des données, pensé pour l'application Android et pour la
publication web (GitHub Pages).

Sorties (dossier data/)
-----------------------
    stations_france.json   stations allégées, clés courtes (~500 Ko au lieu de
                           27 Mo pour le flux brut) : c'est le fichier que
                           l'application mobile peut télécharger.
    meta.json              version, horodatage et compteurs, pour savoir si le
                           cache local de l'application est encore à jour.

Usage
-----
    python3 export_json.py                 # à partir du cache data/stations.json
    python3 export_json.py --refresh       # retélécharge d'abord les données
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import fetch_stations as fs
import prix_carburants as pc

ETAT_GAZOLE = {"Oui": "oui", "Inconnu": "inconnu"}


def station_compacte(record: dict) -> dict:
    """Version courte d'une station, clés abrégées pour limiter le poids."""
    return {
        "id": record["id"],
        "nom": record["nom"],
        "ens": record["enseigne"],
        "adr": record["adresse"],
        "cp": record["code_postal"],
        "vil": record["ville"],
        "dep": record["departement"],
        "lat": round(record["lat"], 5),
        "lng": round(record["lng"], 5),
        "st": record["statut"],
        "h24": record["ouvert_2424"],
        "av": record["avantage_carburant"],
        "cl": record["club"],
        "gz": ETAT_GAZOLE.get(record["gazole_dispo"], "non"),
        "pr": record["gazole_prix"],
        "mj": record["gazole_maj"],
        "gs": record.get("gazole_source", ""),
        "gp": record.get("gazole_plafonne", False),
        "gid": record["gazole_officiel"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="retélécharger les données avant l'export")
    parser.add_argument("--data-dir", default="data", help="dossier des données (défaut : data)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    brut = data_dir / "stations.json"

    if args.refresh or not brut.exists():
        features = fs.fetch_all_stores()
        data_dir.mkdir(parents=True, exist_ok=True)
        brut.write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
    else:
        features = json.loads(brut.read_text(encoding="utf-8"))

    records = [fs.to_record(f) for f in features]
    france = fs.dedupliquer([r for r in records
                             if r["pays"] == "FR" and fs.est_station_service(r)])
    france.sort(key=lambda r: (r["departement"] or "999", r["ville"], r["nom"]))

    if not (data_dir / pc.FICHIER_CACHE).exists():
        print("Prix carburants absents du cache : récupération…")
    pc.enrichir_complet(france, data_dir)

    stations = [station_compacte(r) for r in france]
    prix = sorted(r["gazole_prix"] for r in france if r["gazole_prix"] is not None)
    maintenant = fs.maintenant()

    meta = {
        "genere_le": maintenant.isoformat(timespec="seconds"),
        "version": int(maintenant.timestamp()),
        "total": len(stations),
        "avec_offre": sum(1 for r in france if r["avantage_carburant"]),
        "avec_offre_et_club": sum(1 for r in france if r["avantage_carburant"] and r["club"]),
        "gazole_disponible": sum(1 for r in france if r["gazole_dispo"] == "Oui"),
        "gazole_source_officiel": sum(1 for r in france if r.get("gazole_source") == "officiel"),
        "gazole_source_total": sum(1 for r in france if r.get("gazole_source") == "total"),
        "gazole_plafonne": sum(1 for r in france if r.get("gazole_plafonne")),
        "prix_gazole": {
            "min": prix[0] if prix else None,
            "median": round(statistics.median(prix), 3) if prix else None,
            "max": prix[-1] if prix else None,
        },
        "sources": {
            "stations": "API du localisateur TotalEnergies (Woosmap/PoiFinder)",
            "prix": ("data.economie.gouv.fr — prix-des-carburants-en-france-flux-instantane-v2, "
                     "complété par les fiches publiques des stations du localisateur "
                     "TotalEnergies (prix plafonné national, relevé quotidien) pour les stations "
                     "qui ne déclarent pas leurs prix"),
        },
        "champs": {
            "id": "identifiant station", "nom": "nom", "ens": "enseigne", "adr": "adresse",
            "cp": "code postal", "vil": "ville", "dep": "département", "lat": "latitude",
            "lng": "longitude", "st": "statut", "h24": "ouvert 24/24",
            "av": "opération Avantage Carburant", "cl": "adhésion Club en station",
            "gz": "gazole : oui / non / inconnu", "pr": "prix du gazole en €/L (null si inconnu)",
            "mj": "date de mise à jour du prix",
            "gs": "provenance du prix : officiel (déclaré par la station) ou total (prix plafonné)",
            "gp": "vrai si le prix est un prix plafonné (fiche TotalEnergies)",
            "gid": "identifiant de la station dans le jeu de données officiel des prix",
        },
    }

    json_stations = data_dir / "stations_france.json"
    json_meta = data_dir / "meta.json"
    json_stations.write_text(
        json.dumps({"genere_le": meta["genere_le"], "stations": stations},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    json_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Écrit : {json_stations} ({json_stations.stat().st_size / 1024:.0f} Ko, {len(stations)} stations)")
    print(f"Écrit : {json_meta}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
