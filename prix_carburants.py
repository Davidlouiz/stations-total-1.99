#!/usr/bin/env python3
"""Prix et disponibilité du gazole : récupération du jeu de données officiel
« Prix des carburants en France » (data.economie.gouv.fr, mis à jour plusieurs
fois par jour par les stations elles-mêmes) et rapprochement géographique avec
les stations du réseau TotalEnergies.

Le jeu officiel ne référence que les stations qui déclarent leurs prix : les
petits garages et certains relais du réseau n'y figurent pas. Pour ces stations,
la disponibilité est marquée « Inconnu » plutôt que devinée.

Rapprochement : même code postal d'abord (tolérance 300 m), sinon la station
officielle la plus proche dans un rayon de 150 m.
"""

from __future__ import annotations

import gzip
import json
import math
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = ("https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/"
           "prix-des-carburants-en-france-flux-instantane-v2/records")

# Ne récupérer que les colonnes utiles (le jeu complet fait ~10 000 stations)
CHAMPS = ("id,cp,adresse,ville,latitude,longitude,gazole_prix,gazole_maj,"
          "gazole_rupture_type,carburants_disponibles,carburants_indisponibles")

FICHIER_CACHE = "prix_carburants.json"
AGE_MAX_HEURES = 6  # au-delà, le cache est rafraîchi automatiquement
RAYON_MEME_CP = 300  # mètres
RAYON_SANS_CP = 150  # mètres

ENTETES = {"User-Agent": "Mozilla/5.0 (compatible; stations-total-1.99)"}


def _appel(url: str, timeout: int = 60) -> dict:
    dernier: Exception | None = None
    for tentative in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=ENTETES), timeout=timeout) as reponse:
                brut = reponse.read()
                if reponse.headers.get("Content-Encoding") == "gzip":
                    brut = gzip.decompress(brut)
                return json.loads(brut)
        except Exception as exc:  # réseau, JSON, timeouts
            dernier = exc
            time.sleep(1 + tentative)
    raise SystemExit(f"Échec de l'appel à {url} : {dernier}")


def telecharger(data_dir: Path, refresh: bool = False, verbose: bool = True) -> list[dict]:
    """Retourne les enregistrements officiels, avec cache local."""
    cache = data_dir / FICHIER_CACHE
    if cache.exists() and not refresh:
        age = (time.time() - cache.stat().st_mtime) / 3600
        if age < AGE_MAX_HEURES:
            if verbose:
                print(f"Prix carburants : cache de {age:.1f} h ({cache.name})")
            return json.loads(cache.read_text(encoding="utf-8"))
        if verbose:
            print(f"Prix carburants : cache vieux de {age:.1f} h, rafraîchissement…")

    premiere = _appel(f"{API_URL}?limit=100&offset=0&select={CHAMPS}&order_by=id")
    total = premiere["total_count"]
    enregistrements = list(premiere["results"])
    if verbose:
        print(f"Prix carburants : {total} stations à récupérer…", flush=True)

    for offset in range(100, total, 100):
        enregistrements.extend(
            _appel(f"{API_URL}?limit=100&offset={offset}&select={CHAMPS}&order_by=id")["results"]
        )
        time.sleep(0.05)

    data_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(enregistrements, ensure_ascii=False), encoding="utf-8")
    if verbose:
        print(f"Prix carburants : {len(enregistrements)} stations officielles ({cache.name})")
    return enregistrements


def _distance(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """Distance en mètres (formule de haversine)."""
    d_lat = math.radians(lat_b - lat_a)
    d_lng = math.radians(lng_b - lng_a)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat_a)) * math.cos(math.radians(lat_b)) * math.sin(d_lng / 2) ** 2)
    return 2 * 6371000 * math.asin(math.sqrt(a))


class IndexPrix:
    """Index spatial des stations officielles (par code postal et par cellule)."""

    def __init__(self, enregistrements: list[dict]) -> None:
        self.par_cp: dict[str, list[tuple[float, float, dict]]] = {}
        self.grille: dict[tuple[int, int], list[tuple[float, float, dict]]] = {}
        for brut in enregistrements:
            try:
                lat = float(brut["latitude"]) / 100000
                lng = float(brut["longitude"]) / 100000
            except (KeyError, TypeError, ValueError):
                continue
            entree = (lat, lng, brut)
            self.par_cp.setdefault(str(brut.get("cp") or ""), []).append(entree)
            self.grille.setdefault((int(lat * 100), int(lng * 100)), []).append(entree)

    def _plus_proche(self, lat: float, lng: float, candidats) -> tuple[float, dict] | None:
        meilleure = None
        for c_lat, c_lng, brut in candidats:
            d = _distance(lat, lng, c_lat, c_lng)
            if meilleure is None or d < meilleure[0]:
                meilleure = (d, brut)
        return meilleure

    def chercher(self, lat: float, lng: float, code_postal: str) -> tuple[float, dict] | None:
        """Station officielle correspondante, avec sa distance, ou None."""
        trouvaille = self._plus_proche(lat, lng, self.par_cp.get(code_postal, []))
        if trouvaille and trouvaille[0] <= RAYON_MEME_CP:
            return trouvaille

        # Sans code postal identique : chercher dans les cellules voisines
        voisins = []
        for d_lat in (-1, 0, 1):
            for d_lng in (-1, 0, 1):
                voisins.extend(self.grille.get((int(lat * 100) + d_lat, int(lng * 100) + d_lng), []))
        trouvaille = self._plus_proche(lat, lng, voisins)
        if trouvaille and trouvaille[0] <= RAYON_SANS_CP:
            return trouvaille
        return None


def _date_fr(iso: str | None) -> str:
    """« 2026-09-17T09:06:28+00:00 » -> « 17/09/2026 09:06 »."""
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso[:16].replace("T", " ")


def _etat_gazole(brut: dict) -> str:
    """« Oui », « Non » ou « Inconnu »."""
    disponibles = brut.get("carburants_disponibles") or []
    if "Gazole" in disponibles:
        return "Oui"
    if brut.get("gazole_rupture_type") or "Gazole" in (brut.get("carburants_indisponibles") or []):
        return "Non"
    return "Non (non distribué)"


def enrichir(records: list[dict], enregistrements: list[dict], verbose: bool = True) -> dict:
    """Ajoute à chaque station les champs gazole_* et renvoie quelques statistiques."""
    index = IndexPrix(enregistrements)
    appariées = 0
    distances: list[float] = []

    for record in records:
        trouvaille = index.chercher(record["lat"], record["lng"], record["code_postal"])
        if not trouvaille:
            record.update(
                gazole_dispo="Inconnu",
                gazole_prix=None,
                gazole_maj="",
                gazole_rupture="",
                gazole_officiel=None,
            )
            continue

        distance, brut = trouvaille
        appariées += 1
        distances.append(distance)
        rupture = (brut.get("gazole_rupture_type") or "").capitalize()
        record.update(
            gazole_dispo=_etat_gazole(brut),
            gazole_prix=brut.get("gazole_prix"),
            gazole_maj=_date_fr(brut.get("gazole_maj")),
            gazole_rupture=rupture,
            gazole_officiel=brut.get("id"),
        )

    stats = {
        "appariées": appariées,
        "total": len(records),
        "distance_médiane": sorted(distances)[len(distances) // 2] if distances else 0,
        "gazole_oui": sum(1 for r in records if r["gazole_dispo"] == "Oui"),
        "gazole_non": sum(1 for r in records if r["gazole_dispo"].startswith("Non")),
        "gazole_inconnu": sum(1 for r in records if r["gazole_dispo"] == "Inconnu"),
    }
    if verbose:
        print(f"Gazole : {stats['appariées']}/{stats['total']} stations rapprochées "
              f"(distance médiane {stats['distance_médiane']:.0f} m) — "
              f"{stats['gazole_oui']} avec gazole, {stats['gazole_non']} sans, "
              f"{stats['gazole_inconnu']} sans donnée")
    return stats


def horodatage() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
