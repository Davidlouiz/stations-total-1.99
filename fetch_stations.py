#!/usr/bin/env python3
"""Liste des stations du réseau TotalEnergies, avec distinction des stations
qui participent à l'Opération Avantage Carburant (prix plafonné ~1,99 €/L).

Source
------
API du localisateur de stations TotalEnergies (Woosmap / PoiFinder), celle
utilisée par l'application mobile et par https://services.totalenergies.fr/stations.

Le filtre « Avantage Carburant » de la catégorie « Club TotalEnergies » de
l'application correspond exactement au tag `OpeAvantageCarburant` renvoyé par
l'API pour chaque station.

Usage
-----
    python3 fetch_stations.py                 # France métropolitaine + DROM
    python3 fetch_stations.py --tous-pays     # monde entier
    python3 fetch_stations.py --refresh       # ignore le cache JSON

Sorties (dossier data/)
-----------------------
    stations.json   : toutes les stations récupérées (brut, réutilisable)
    stations.csv    : la liste filtrée, séparateur « ; », prête pour Excel
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import prix_carburants

# Les machines des runners GitHub Actions sont en UTC : on horodate explicitement
# en heure de Paris, avec repli sur l'heure locale si la base de fuseaux manque.
try:
    from zoneinfo import ZoneInfo

    FUSEAU_PARIS = ZoneInfo("Europe/Paris")
except Exception:  # pragma: no cover - dépend du système
    FUSEAU_PARIS = None


def maintenant() -> datetime:
    """Heure de Paris (repli : heure locale de la machine)."""
    return datetime.now(FUSEAU_PARIS) if FUSEAU_PARIS else datetime.now()

# --- API du localisateur TotalEnergies ---------------------------------------
API_URL = "https://api.woosmap.com/stores/"
API_KEY = "woos-c236c6f3-31fe-3118-82e7-a19ca42466f7"  # clé publique du site
REFERER = "https://services.totalenergies.fr/"
PAGE_SIZE = 100
PAUSE = 0.1  # secondes entre deux pages, pour rester courtois

# --- Tags significatifs ------------------------------------------------------
TAG_AVANTAGE = "OpeAvantageCarburant"  # Opération Avantage Carburant
TAG_CLUB = "Adhesionstation"  # adhésion au Club TotalEnergies

BRANDS = {
    "TOTAL": "Total",
    "ACCESS": "TotalEnergies Access",
    "ELAN": "Élan",
    "ELF": "Elf",
    "ELFMA": "Elf",
    "TWASH": "TotalEnergies Wash",
    "ARS": "ARS",
    "RTS": "RTS",
    "QAS": "QAS",
    "HMS": "HMS",
    "DIS": "Distributeur",
}

STATUSES = {
    "OPEN": "Ouvert",
    "TEMPORARILY_CLOSED": "Fermé temporairement",
    "INITIAL": "Statut inconnu",
}

POI_TYPES = {
    "FUELING": "Station-service",
    "WASH": "Centre de lavage",
    "EV": "Recharge électrique",
    "AIRPORT": "Aéroport",
    "AIRFIELD": "Aérodrome",
    "GARAGE": "Garage",
    "DISTRIBUTOR": "Distributeur",
}

# Réseaux qui vendent du carburant au grand public (hors lavage / bornes seules)
BUSINESS_STATION = {"retail", "general_trade"}


def est_station_service(record: dict) -> bool:
    """Vrai si le point de vente distribue du carburant au grand public."""
    return record.get("business") in BUSINESS_STATION


# Un même lieu peut être décrit plusieurs fois par l'API, une fiche par type de
# point de vente (« station-service », « centre de lavage »...). On garde une
# seule ligne par lieu : la fiche carburant en priorité, en fusionnant les offres.
ORDRE_POI = ["FUELING", "DISTRIBUTOR", "GARAGE", "AIRPORT", "AIRFIELD", "EV", "WASH"]


def _rang_poi(record: dict) -> int:
    try:
        return ORDRE_POI.index(record.get("poitype", ""))
    except ValueError:
        return len(ORDRE_POI)


def dedupliquer(records: list[dict], garder_lavages_seuls: bool = False) -> list[dict]:
    """Une ligne par lieu (location_id), attributs fusionnés.

    Un lieu qui n'a aucune fiche « station-service » (lavage seul) est écarté,
    sauf si `garder_lavages_seuls` est vrai.
    """
    groupes: dict[str, list[dict]] = {}
    for record in records:
        groupes.setdefault(record["id"], []).append(record)

    fusionnees = []
    for groupe in groupes.values():
        principal = dict(min(groupe, key=_rang_poi))
        if not garder_lavages_seuls and principal["poitype"] != "FUELING":
            continue
        principal["avantage_carburant"] = any(r["avantage_carburant"] for r in groupe)
        principal["club"] = any(r["club"] for r in groupe)
        principal["ouvert_2424"] = any(r["ouvert_2424"] for r in groupe)
        offres = sorted({o.strip() for r in groupe
                         for o in r["autres_offres"].split(",") if o.strip()})
        principal["autres_offres"] = ", ".join(offres)
        principal["types_lieu"] = " + ".join(sorted({r["type"] for r in groupe}))
        fusionnees.append(principal)
    return fusionnees

# Autres offres / avantages Club visibles dans les tags
OTHER_OFFERS = re.compile(r"^(Ope|Offre|Operation|Adhesionstation|ClubTotalEnergies)", re.I)

CSV_COLUMNS = [
    "Enseigne",
    "Type",
    "Lieu",
    "Nom",
    "Adresse",
    "Code postal",
    "Ville",
    "Département",
    "Latitude",
    "Longitude",
    "Statut",
    "Ouvert 24/24",
    "Avantage Carburant",
    "Adhésion Club en station",
    "Gazole disponible",
    "Prix gazole (€/L)",
    "MAJ gazole",
    "Rupture gazole",
    "Autres offres",
    "ID station",
]


def http_get_json(url: str, timeout: int = 30) -> dict:
    """GET avec en-têtes du site + 3 tentatives."""
    last_error: Exception | None = None
    for attempt in range(3):
        req = urllib.request.Request(
            url,
            headers={
                "Referer": REFERER,
                "Origin": REFERER.rstrip("/"),
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; stations-total-1.99)",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return json.load(response)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            time.sleep(1 + attempt)
    raise SystemExit(f"Échec de l'appel API {url} : {last_error}")


def fetch_all_stores(verbose: bool = True) -> list[dict]:
    """Récupère la totalité des stations en parcourant la pagination."""
    first = http_get_json(f"{API_URL}?key={API_KEY}&storesByPage={PAGE_SIZE}&page=1")
    page_count = first["pagination"]["pageCount"]
    features: list[dict] = list(first["features"])
    if verbose:
        print(f"{page_count} pages à récupérer…", flush=True)

    for page in range(2, page_count + 1):
        data = http_get_json(f"{API_URL}?key={API_KEY}&storesByPage={PAGE_SIZE}&page={page}")
        features.extend(data["features"])
        if verbose and page % 20 == 0:
            print(f"  page {page}/{page_count} ({len(features)} stations)", flush=True)
        time.sleep(PAUSE)

    if verbose:
        print(f"{len(features)} stations récupérées au total.")
    return features


def department(props: dict) -> str:
    """Numéro de département (pour la France), sinon chaîne vide."""
    state = (props["user_properties"].get("state") or "").strip().upper()
    if re.fullmatch(r"\d{1,3}|2A|2B", state):
        return state
    zipcode = (props["address"].get("zipcode") or "").strip()
    return zipcode[:2] if zipcode[:2].isdigit() else ""


def to_record(feature: dict) -> dict:
    """Transforme une feature GeoJSON en enregistrement plat exploitable."""
    props = feature["properties"]
    user = props["user_properties"]
    address = props["address"]
    tags = props.get("tags", [])
    lng, lat = feature["geometry"]["coordinates"]

    return {
        "id": user.get("location_id") or props.get("store_id", ""),
        "brand": user.get("brand", ""),
        "enseigne": BRANDS.get(user.get("brand", ""), user.get("brand", "")),
        "poitype": user.get("poiType", ""),
        "type": POI_TYPES.get(user.get("poiType", ""), user.get("poiType", "")),
        "business": user.get("business", ""),
        "nom": (props.get("name") or "").strip(),
        "adresse": " ".join(address.get("lines") or []).strip(),
        "code_postal": (address.get("zipcode") or "").strip(),
        "ville": (address.get("city") or "").strip(),
        "departement": department(props),
        "pays": (address.get("country_code") or "").upper(),
        "lat": lat,
        "lng": lng,
        "statut": STATUSES.get(user.get("status", ""), user.get("status", "")),
        "ouvert_2424": "2424openinghours" in tags,
        "avantage_carburant": TAG_AVANTAGE in tags,
        "club": TAG_CLUB in tags,
        "autres_offres": ", ".join(
            sorted(t for t in tags if OTHER_OFFERS.match(t) and t not in (TAG_AVANTAGE, TAG_CLUB))
        ),
        "tags": tags,
        "site": (props.get("contact") or {}).get("website") or "",
        "maj": props.get("last_updated", ""),
    }


def write_csv(rows: list[dict], path: Path) -> None:
    """CSV séparateur « ; » + BOM : ouverture directe dans Excel français."""
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(
                [
                    row["enseigne"],
                    row["type"],
                    row.get("types_lieu", row["type"]),
                    row["nom"],
                    row["adresse"],
                    row["code_postal"],
                    row["ville"],
                    row["departement"],
                    f"{row['lat']:.6f}",
                    f"{row['lng']:.6f}",
                    row["statut"],
                    "Oui" if row["ouvert_2424"] else "Non",
                    "OUI" if row["avantage_carburant"] else "NON",
                    "Oui" if row["club"] else "Non",
                    row["gazole_dispo"],
                    "" if row["gazole_prix"] is None else f"{row['gazole_prix']:.3f}".replace(".", ","),
                    row["gazole_maj"],
                    row["gazole_rupture"],
                    row["autres_offres"],
                    row["id"],
                ]
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tous-pays", action="store_true", help="ne pas filtrer sur la France")
    parser.add_argument("--tous-poi", action="store_true",
                        help="inclure aussi les lavages, bornes de recharge et sites aviation")
    parser.add_argument("--refresh", action="store_true", help="ignorer le cache data/stations.json")
    parser.add_argument("--sans-prix", action="store_true",
                        help="ne pas récupérer les prix gazole (données officielles)")
    parser.add_argument("--data-dir", default="data", help="dossier de sortie (défaut : data)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    raw_path = data_dir / "stations.json"

    if raw_path.exists() and not args.refresh:
        print(f"Cache utilisé : {raw_path} (--refresh pour forcer la mise à jour)")
        features = json.loads(raw_path.read_text(encoding="utf-8"))
    else:
        features = fetch_all_stores()
        raw_path.write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
        print(f"Écrit : {raw_path}")

    records = [to_record(f) for f in features]
    if not args.tous_poi:
        records = [r for r in records if est_station_service(r)]
    records = dedupliquer(records, garder_lavages_seuls=args.tous_poi)

    if args.tous_pays:
        selected, suffix = records, "monde"
    else:
        selected = [r for r in records if r["pays"] == "FR"]
        suffix = "france"

    selected.sort(key=lambda r: (r["departement"] or "999", r["ville"], r["nom"]))

    if args.sans_prix:
        for record in selected:
            record.update(gazole_dispo="", gazole_prix=None, gazole_maj="",
                          gazole_rupture="", gazole_officiel=None)
    else:
        print()
        prix_carburants.enrichir(selected, prix_carburants.telecharger(data_dir, refresh=args.refresh))

    csv_path = data_dir / f"stations_{suffix}.csv"
    write_csv(selected, csv_path)

    with_offer = [r for r in selected if r["avantage_carburant"]]
    open_with_offer = [r for r in with_offer if r["statut"] == "Ouvert"]

    print()
    print(f"Stations du réseau ({'monde' if args.tous_pays else 'France'}) : {len(selected)}")
    print(f"  dont Avantage Carburant : {len(with_offer)} (dont {len(open_with_offer)} ouvertes)")
    print(f"  sans Avantage Carburant : {len(selected) - len(with_offer)}")
    if not args.sans_prix:
        avec_gazole = [r for r in selected if r["gazole_dispo"] == "Oui"]
        print(f"  gazole disponible : {len(avec_gazole)}")
        prix = sorted(r["gazole_prix"] for r in avec_gazole if r["gazole_prix"] is not None)
        if prix:
            print(f"  prix gazole : de {prix[0]:.3f} à {prix[-1]:.3f} €/L (médian {prix[len(prix) // 2]:.3f})")
    par_enseigne: dict[str, int] = {}
    for record in selected:
        par_enseigne[record["enseigne"]] = par_enseigne.get(record["enseigne"], 0) + 1
    for enseigne, count in sorted(par_enseigne.items(), key=lambda kv: -kv[1]):
        offer = sum(1 for r in with_offer if r["enseigne"] == enseigne)
        print(f"  - {enseigne:<24} {count:>5} stations, {offer:>5} avec l'offre")
    print(f"Écrit : {csv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
