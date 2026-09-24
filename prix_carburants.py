#!/usr/bin/env python3
"""Prix et disponibilité du gazole : récupération du jeu de données officiel
« Prix des carburants en France » (data.economie.gouv.fr, mis à jour plusieurs
fois par jour par les stations elles-mêmes) et rapprochement géographique avec
les stations du réseau TotalEnergies.

Le jeu officiel ne référence que les stations qui déclarent leurs prix : les
petits garages et certains relais du réseau n'y figurent pas. Pour ces stations,
la disponibilité est marquée « Inconnu » plutôt que devinée.

Deuxième source, complémentaire : la fiche publique d'une station sur le
localisateur TotalEnergies (API « PoiFinder ») donne le prix plafonné national
et la disponibilité carburant par carburant. Elle ne couvre que les stations
de type « retail » (les relais et garages de type « general_trade » n'y sont
pas), elle est mise à jour une fois par jour et le prix affiché est un prix
plafonné national : ces données sont donc marquées `gazole_source = "total"`
et affichées différemment sur la carte.

Rapprochement : même code postal d'abord (tolérance 300 m), sinon la station
officielle la plus proche dans un rayon de 150 m.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import time
import urllib.error
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

# --- Source complémentaire : fiche publique d'une station (API TotalEnergies) --
# Endpoint utilisé par le localisateur lui-même pour afficher une station.
# Répond 404 pour les stations absentes du référentiel prix (relais, garages).
API_TOTAL = ("https://apis.poifinder.alzp.tgscloud.net/poi-finder-store-locator-back"
             "/api/v1/point-of-interest")

# La clé d'API est PUBLIQUE (elle est servie telle quelle par le bundle du
# localisateur). Elle n'est pas écrite ici : elle est relue à l'exécution, ce qui
# évite de figer une valeur révocable et de la faire ressembler à un secret.
BUNDLE_TOTAL = "https://customdevs.woosmap.com/total/front.js"
MOTIF_CLE_TOTAL = re.compile(r'totalKey\s*:\s*"([^"]+)"')
FICHIER_TOTAL = "prix_total.json"
AGE_MAX_TOTAL = 6  # heures
CODE_GAZOLE = "GO"
PAUSE_TOTAL = 0.1  # secondes entre deux fiches, pour rester courtois

_cle_total: str | None = None


def cle_total() -> str:
    """Clé d'API publique du localisateur, lue dans le bundle du site (ou « »)."""
    global _cle_total
    if _cle_total is None:
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(BUNDLE_TOTAL, headers=ENTETES), timeout=30) as reponse:
                trouve = MOTIF_CLE_TOTAL.search(reponse.read().decode("utf-8", "replace"))
                _cle_total = trouve.group(1) if trouve else ""
        except Exception:  # réseau, bundle déplacé : on se passe du complément
            _cle_total = ""
    return _cle_total


def entetes_total() -> dict[str, str]:
    """En-têtes de l'appel aux fiches station (vide si la clé est introuvable)."""
    cle = cle_total()
    if not cle:
        return {}
    return {
        "API-Key": cle,
        "Accept": "application/json",
        "Referer": "https://services.totalenergies.fr/",
        "User-Agent": "Mozilla/5.0 (compatible; stations-total-1.99)",
    }


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
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")
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


# --- Fiches station de l'API TotalEnergies -----------------------------------
def _fiche_total(reference: str, timeout: int = 20) -> tuple[str, dict | None]:
    """Lit une fiche station. Renvoie (« ok » | « absente » | « erreur », données)."""
    entetes = entetes_total()
    if not entetes:  # clé illisible : on renonce au complément, sans casser la suite
        return "erreur", None
    url = f"{API_TOTAL}?location_id={reference}&type=FUELING"
    for tentative in range(2):
        try:
            requete = urllib.request.Request(url, headers=entetes)
            with urllib.request.urlopen(requete, timeout=timeout) as reponse:
                return "ok", json.loads(reponse.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            # 404 : station absente du référentiel prix (relais, garages)
            # 400/401/403 : requête ou clé refusée, inutile d'insister
            if exc.code in (400, 401, 403, 404):
                return ("absente" if exc.code == 404 else "erreur"), None
            time.sleep(0.5 + tentative)
        except Exception:  # réseau, JSON invalide, délai dépassé
            time.sleep(0.5 + tentative)
    return "erreur", None


def _gazole_total(donnees: dict) -> dict:
    """{prix, dispo, maj} pour le gazole, ou {} si la fiche ne le mentionne pas."""
    for produit in donnees.get("products_and_services") or []:
        if (produit.get("product_code") or "").upper() != CODE_GAZOLE:
            continue
        return {
            "prix": produit.get("price"),
            "dispo": (produit.get("availability_status") or "").upper(),
            "maj": _date_fr(produit.get("price_update_date_time")),
        }
    return {}


def telecharger_total(data_dir: Path, references: list[str], refresh: bool = False,
                      verbose: bool = True) -> dict[str, dict]:
    """Fiches des stations demandées (cache local), indexées par référence.

    Une référence absente du référentiel prix est mémorisée vide : inutile de
    la redemander. Les erreurs réseau ne sont pas mises en cache (nouvel essai
    à la prochaine exécution).
    """
    cache = data_dir / FICHIER_TOTAL
    fiches: dict[str, dict] = {}
    if cache.exists() and not refresh:
        try:
            contenu = json.loads(cache.read_text(encoding="utf-8"))
            age = (time.time() - contenu.get("genere", 0)) / 3600
            if age < AGE_MAX_TOTAL:
                fiches = contenu.get("fiches") or {}
                if verbose:
                    print(f"Fiches TotalEnergies : cache de {age:.1f} h ({len(fiches)} fiches)")
        except (OSError, ValueError):
            fiches = {}

    a_charger = [r for r in references if r not in fiches]
    if not a_charger:
        return fiches
    if not cle_total():
        if verbose:
            print("Fiches TotalEnergies : clé d'API introuvable — complément ignoré")
        return fiches

    if verbose:
        print(f"Fiches TotalEnergies : {len(a_charger)} stations à interroger…", flush=True)
    absentes = erreurs = avec_prix = 0
    for rang, reference in enumerate(a_charger, 1):
        etat, donnees = _fiche_total(reference)
        if etat == "ok":
            fiche = _gazole_total(donnees)
            fiches[reference] = fiche
            avec_prix += fiche.get("prix") is not None
        elif etat == "absente":
            fiches[reference] = {}
            absentes += 1
        else:
            erreurs += 1
        if verbose and rang % 50 == 0:
            print(f"  {rang}/{len(a_charger)}…", flush=True)
        time.sleep(PAUSE_TOTAL)

    data_dir.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({"genere": time.time(), "fiches": fiches}, ensure_ascii=False),
                     encoding="utf-8")
    if verbose:
        print(f"Fiches TotalEnergies : {avec_prix} avec un prix, {absentes} absentes du référentiel, "
              f"{erreurs} erreur(s) réseau")
    return fiches


def _appliquer_total(records: list[dict], fiches: dict[str, dict], verbose: bool = True) -> dict:
    """Complète les stations que le jeu officiel ne couvre pas.

    Le prix renvoyé par le localisateur est un prix plafonné national : il est
    marqué comme tel (`gazole_source = "total"`, `gazole_plafonne = True`) pour
    ne jamais être confondu avec un prix relevé à la pompe.
    """
    completees = non_distribue = 0
    for record in records:
        if record["gazole_dispo"] != "Inconnu" or record.get("business") != "retail":
            continue
        fiche = fiches.get(record["id"]) or {}
        if fiche.get("prix") is not None:
            record.update(gazole_dispo="Oui", gazole_prix=fiche["prix"],
                          gazole_maj=fiche.get("maj") or "", gazole_rupture="",
                          gazole_source="total", gazole_plafonne=True)
            completees += 1
        elif fiche.get("dispo") == "UNAVAILABLE":
            record.update(gazole_dispo="Non (non distribué)", gazole_source="total",
                          gazole_plafonne=False)
            non_distribue += 1

    if verbose and (completees or non_distribue):
        print(f"Fiches TotalEnergies : {completees} station(s) complétée(s) "
              f"(prix plafonné), {non_distribue} sans gazole à la vente")
    return {"total_completees": completees, "total_non_distribue": non_distribue}


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
                gazole_source="",
                gazole_plafonne=False,
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
            gazole_source="officiel",
            gazole_plafonne=False,
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


def enrichir_complet(records: list[dict], data_dir: Path, refresh: bool = False,
                     verbose: bool = True) -> dict:
    """Jeu officiel, puis complément par les fiches du localisateur TotalEnergies.

    Point d'entrée à utiliser : il gère les deux sources et leurs caches.
    """
    officiels = telecharger(data_dir, refresh=refresh, verbose=verbose)
    stats = enrichir(records, officiels, verbose=verbose)

    # Seules les stations « retail » de France absentes du jeu officiel ont une
    # fiche : les relais et garages (« general_trade ») n'existent pas côté Total.
    references = [r["id"] for r in records
                  if r["gazole_dispo"] == "Inconnu" and r.get("business") == "retail"
                  and r.get("pays") == "FR"]
    if references:
        stats.update(_appliquer_total(records, telecharger_total(data_dir, references,
                                                                 refresh=refresh, verbose=verbose),
                                      verbose=verbose))
        stats["gazole_oui"] = sum(1 for r in records if r["gazole_dispo"] == "Oui")
        stats["gazole_inconnu"] = sum(1 for r in records if r["gazole_dispo"] == "Inconnu")
    return stats


def horodatage() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%d/%m/%Y %H:%M")
