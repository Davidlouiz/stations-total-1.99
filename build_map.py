#!/usr/bin/env python3
"""Génère une carte HTML interactive des stations du réseau TotalEnergies,
avec un interrupteur « Avantage Carburant » (stations participantes ou non).

Usage
-----
    python3 build_map.py                     # lit le cache data/stations.json
    python3 build_map.py --refresh           # retélécharge les données d'abord

Sortie : data/carte.html (fichier autonome, à ouvrir dans un navigateur).
La carte et la liste utilisent Leaflet + OpenStreetMap : une connexion
internet est nécessaire à l'affichage des fonds de carte.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import fetch_stations as fs
import prix_carburants

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stations TotalEnergies — Avantage Carburant</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>
  :root { --vert:#1a9e4b; --gris:#9aa0a6; --rand:#e2001a; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif; color:#1c1c1c; }
  header { background:var(--rand); color:#fff; padding:10px 16px; }
  header h1 { margin:0; font-size:1.05rem; font-weight:600; }
  header p { margin:4px 0 0; font-size:.78rem; opacity:.9; }
  #filtres { display:flex; flex-wrap:wrap; gap:8px; align-items:center;
             padding:8px 16px; background:#f5f5f5; border-bottom:1px solid #ddd; }
  #filtres label { font-size:.78rem; color:#444; display:flex; gap:4px; align-items:center; }
  input,select { font:inherit; font-size:.8rem; padding:4px 6px; border:1px solid #ccc; border-radius:4px; background:#fff; }
  input[type=search] { width:180px; }
  .seg { display:flex; border:1px solid #ccc; border-radius:4px; overflow:hidden; }
  .seg button { font:inherit; font-size:.78rem; padding:4px 10px; border:0; background:#fff; cursor:pointer; }
  .seg button[aria-pressed=true] { background:var(--rand); color:#fff; }
  .bouton-geoloc { font:inherit; font-size:.8rem; padding:4px 10px; border:0; border-radius:4px;
                   background:var(--rand); color:#fff; cursor:pointer; }
  .bouton-geoloc:hover { opacity:.9; }
  .bouton-geoloc:disabled { opacity:.5; cursor:progress; }
  .statut-direct { font-size:.75rem; color:#555; }
  #compteur { font-size:.78rem; margin-left:auto; color:#333; }
  main { display:flex; height:calc(100vh - 104px); }
  #carte { flex:1 1 auto; min-width:0; }
  aside { width:390px; max-width:42vw; overflow:auto; border-left:1px solid #ddd; }
  .item { padding:8px 12px; border-bottom:1px solid #eee; cursor:pointer; font-size:.82rem; }
  .item:hover { background:#f7f7f7; }
  .item .nom { font-weight:600; }
  .item .meta { color:#666; font-size:.75rem; }
  .badge { display:inline-block; font-size:.68rem; font-weight:700; padding:1px 6px;
           border-radius:10px; color:#fff; vertical-align:middle; }
  .badge.oui { background:var(--vert); }
  .badge.non { background:var(--gris); }
  .badge.rouge { background:#d93025; }
  .badge.ferme { background:var(--rand); margin-left:4px; }
  .pastille { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:4px; }
  @media (max-width:820px){
    #filtres { gap:6px; }
    input[type=search] { width:100%; }
    main{flex-direction:column;height:auto;} #carte{height:55vh;} aside{width:auto;max-width:none;}
  }
</style>
</head>
<body>
<header>
  <h1>Stations TotalEnergies — Opération Avantage Carburant</h1>
  <p>Source : localisateur TotalEnergies · données générées le __DATE__ · __TOTAL__ stations en France</p>
</header>

<div id="filtres">
  <div class="seg" id="segAvantage">
    <button data-valeur="tous" aria-pressed="true">Toutes</button>
    <button data-valeur="oui" aria-pressed="false">Avantage Carburant</button>
    <button data-valeur="non" aria-pressed="false">Sans l'offre</button>
  </div>
  <label>Enseigne <select id="enseigne"><option value="">toutes</option></select></label>
  <label>Département <select id="departement"><option value="">tous</option></select></label>
  <label>Trier par
    <select id="tri">
      <option value="distance">distance au centre de l'écran</option>
      <option value="departement">département</option>
      <option value="ville">ville</option>
      <option value="enseigne">enseigne</option>
      <option value="prix">prix du gazole</option>
    </select>
  </label>
  <label>Limite <input type="number" id="limite" min="1" max="500" step="1" value="50" style="width:66px"></label>
  <label>Fond
    <select id="fond">
      <option value="osm">Plan OpenStreetMap</option>
      <option value="ign">Plan IGN</option>
      <option value="clair">Fond clair</option>
    </select>
  </label>
  <label>Gazole
    <select id="gazole">
      <option value="">toutes</option>
      <option value="oui">disponible</option>
      <option value="non">indisponible</option>
      <option value="inconnu">donnée inconnue</option>
    </select>
  </label>
  <label>Prix max <input type="number" id="prixMax" min="0" step="0.01" placeholder="€/L" style="width:72px"></label>
  <label><input type="checkbox" id="masquerFermes" checked> masquer les fermées</label>
  <label><input type="checkbox" id="clubRequis"> adhésion Club en station</label>
  <button type="button" id="geoloc" class="bouton-geoloc">Autour de moi</button>
  <button type="button" id="enDirect" class="bouton-geoloc">Vérifier en direct</button>
  <span id="statutDirect" class="statut-direct"></span>
  <label><input type="search" id="recherche" placeholder="ville, nom, code postal…"></label>
  <span id="compteur"></span>
</div>

<main>
  <div id="carte"></div>
  <aside id="liste"></aside>
</main>

<script id="donnees" type="application/json">__DATA__</script>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const STATIONS = JSON.parse(document.getElementById('donnees').textContent);
const carte = L.map('carte').setView([46.6, 2.4], 6);

// Fonds de carte. Les tuiles « tile.openstreetmap.fr » sont refusées (HTTP 403)
// depuis une page ouverte en file:// : on utilise OSM standard, avec un secours
// automatique sur le Plan IGN si les tuiles ne chargent pas.
const FONDS = {
  osm: {
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    maxZoom: 19,
    attribution: '&copy; contributeurs <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  },
  ign: {
    url: 'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}',
    maxZoom: 18,
    attribution: '&copy; <a href="https://geoservices.ign.fr/">IGN</a>'
  },
  clair: {
    url: 'https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
    maxZoom: 19,
    attribution: '&copy; OpenStreetMap, &copy; CARTO'
  }
};

let coucheFond = null;

function afficherFond(cle) {
  if (coucheFond) carte.removeLayer(coucheFond);
  coucheFond = L.tileLayer(FONDS[cle].url, {
    maxZoom: FONDS[cle].maxZoom,
    attribution: FONDS[cle].attribution
  });
  coucheFond.on('tileerror', () => {
    if (cle === 'osm') afficherFond('ign'); // fond par défaut indisponible -> secours
  });
  coucheFond.addTo(carte);
  document.getElementById('fond').value = cle;
}

afficherFond('osm');
document.getElementById('fond').addEventListener('change', e => afficherFond(e.target.value));

const couleur = s => s.avantage_carburant ? '#1a9e4b' : '#9aa0a6';
const couche = L.layerGroup().addTo(carte);

// Marqueurs de la liste affichée, et station dont l'infobulle doit s'ouvrir
// après un recalcul (le tri par distance réagit au déplacement de la carte :
// les marqueurs sont recréés, donc l'infobulle doit être rouverte ensuite).
const marqueurs = new Map();
let aTirer = null;

// Centre de l'écran (mis à jour à chaque déplacement de la carte)
let centre = carte.getCenter();
let modeDistance = true;

function distanceKm(lat1, lng1, lat2, lng2) {
  const R = 6371, rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad, dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

const formaterKm = km => km < 1 ? Math.round(km * 1000) + ' m'
                                : km.toFixed(km < 10 ? 1 : 0).replace('.', ',') + ' km';

// Libellé gazole : prix si disponible, sinon état, sinon donnée absente
function texteGazole(s) {
  if (s.gazole_dispo === 'Oui') {
    return s.gazole_prix === null ? 'Gazole disponible (prix non communiqué)'
                                  : 'Gazole ' + s.gazole_prix.toFixed(3).replace('.', ',') + ' €/L';
  }
  if (s.gazole_dispo === 'Inconnu') return 'Gazole : donnée non disponible';
  return 'Gazole indisponible' + (s.gazole_rupture ? ' (' + s.gazole_rupture.toLowerCase() + ')' : '');
}

function badgeGazole(s) {
  if (s.gazole_dispo === 'Oui') return '<span class="badge oui">' + texteGazole(s) + '</span>';
  if (s.gazole_dispo === 'Inconnu') return '<span class="badge non">Gazole : donnée non disponible</span>';
  return '<span class="badge rouge">' + texteGazole(s) + '</span>';
}

// Remplissage des listes déroulantes
const enseignes = [...new Set(STATIONS.map(s => s.enseigne))].sort();
enseignes.forEach(e => document.getElementById('enseigne').add(new Option(e, e)));
const departements = [...new Set(STATIONS.map(s => s.departement).filter(Boolean))].sort();
departements.forEach(d => document.getElementById('departement').add(new Option(d, d)));

let filtreAvantage = 'tous';

function stationsFiltrees() {
  const enseigne = document.getElementById('enseigne').value;
  const departement = document.getElementById('departement').value;
  const tri = document.getElementById('tri').value;
  const masquerFermes = document.getElementById('masquerFermes').checked;
  const texte = document.getElementById('recherche').value.trim().toLowerCase();
  const gazole = document.getElementById('gazole').value;
  const prixMax = parseFloat(document.getElementById('prixMax').value);
  const clubRequis = document.getElementById('clubRequis').checked;
  const limite = parseInt(document.getElementById('limite').value, 10);
  modeDistance = (tri === 'distance');

  const resultat = STATIONS.filter(s => {
    if (filtreAvantage === 'oui' && !s.avantage_carburant) return false;
    if (filtreAvantage === 'non' && s.avantage_carburant) return false;
    if (enseigne && s.enseigne !== enseigne) return false;
    if (departement && s.departement !== departement) return false;
    if (masquerFermes && s.statut !== 'Ouvert') return false;
    if (clubRequis && !s.club) return false;
    if (gazole === 'oui' && s.gazole_dispo !== 'Oui') return false;
    if (gazole === 'non' && !s.gazole_dispo.startsWith('Non')) return false;
    if (gazole === 'inconnu' && s.gazole_dispo !== 'Inconnu') return false;
    if (!isNaN(prixMax) && (s.gazole_prix === null || s.gazole_prix > prixMax)) return false;
    if (texte) {
      const champs = (s.ville + ' ' + s.nom + ' ' + s.code_postal + ' ' + s.adresse).toLowerCase();
      if (!champs.includes(texte)) return false;
    }
    return true;
  });

  resultat.forEach(s => {
    s.distanceKm = distanceKm(centre.lat, centre.lng, s.lat, s.lng);
  });

  resultat.sort((a, b) => {
    if (tri === 'ville') return a.ville.localeCompare(b.ville, 'fr');
    if (tri === 'enseigne') return a.enseigne.localeCompare(b.enseigne, 'fr') || a.ville.localeCompare(b.ville, 'fr');
    if (tri === 'prix') {
      const pa = a.gazole_prix, pb = b.gazole_prix;
      if (pa === null && pb === null) return a.distanceKm - b.distanceKm;
      if (pa === null) return 1;
      if (pb === null) return -1;
      return pa - pb || a.distanceKm - b.distanceKm;
    }
    if (tri === 'distance') return a.distanceKm - b.distanceKm;
    return (a.departement || '999').localeCompare(b.departement || '999', 'fr') || a.ville.localeCompare(b.ville, 'fr');
  });

  return (!isNaN(limite) && limite > 0) ? resultat.slice(0, limite) : resultat;
}

function afficher() {
  const liste = stationsFiltrees();
  const avecOffre = liste.filter(s => s.avantage_carburant).length;
  const avecGazole = liste.filter(s => s.gazole_dispo === 'Oui').length;
  document.getElementById('compteur').textContent =
    liste.length + " station(s) — " + avecOffre + " avec l'Avantage Carburant — "
    + avecGazole + " avec gazole disponible"
    + (modeDistance ? " — centre de l'écran : " + centre.lat.toFixed(3) + ', ' + centre.lng.toFixed(3) : '');

  couche.clearLayers();
  marqueurs.clear();
  const conteneur = document.getElementById('liste');
  conteneur.textContent = '';
  const fragment = document.createDocumentFragment();

  liste.forEach(s => {
    const marqueur = L.circleMarker([s.lat, s.lng], {
      radius: s.avantage_carburant ? 6 : 4,
      color: '#fff', weight: 1, fillColor: couleur(s), fillOpacity: .9
    });
    marqueur.bindPopup(
      '<strong>' + s.nom + '</strong><br>' + s.enseigne + '<br>' +
      s.adresse + '<br>' + s.code_postal + ' ' + s.ville + '<br>' +
      (s.avantage_carburant
        ? '<span class="badge oui">AVANTAGE CARBURANT</span>'
        : '<span class="badge non">sans l&#39;offre</span>') +
      (s.statut === 'Ouvert' ? '' : ' <span class="badge ferme">' + s.statut + '</span>') +
      '<br>' + badgeGazole(s) +
      '<br><a href="https://www.google.com/maps/dir/?api=1&destination=' + s.lat + ',' + s.lng +
      '" target="_blank" rel="noopener">Itinéraire</a> · ' +
      '<a href="https://locator.totalenergies.com/' + s.id +
      '?type=FUELING&amp;business_type=RETAIL" target="_blank" rel="noopener">Fiche station</a>'
    );
    marqueur.addTo(couche);
    marqueurs.set(s.id, marqueur);

    const item = document.createElement('div');
    item.className = 'item';
    item.innerHTML =
      '<div class="nom"><span class="pastille" style="background:' + couleur(s) + '"></span>' +
      s.ville + (s.code_postal ? ' (' + s.code_postal + ')' : '') + '</div>' +
      '<div class="meta">' + s.nom + ' · ' + s.enseigne + '</div>' +
      '<div class="meta">' + s.adresse + (modeDistance ? ' · <strong>' + formaterKm(s.distanceKm) + '</strong>' : '') + '</div>' +
      '<div>' + (s.avantage_carburant
        ? '<span class="badge oui">Avantage Carburant</span>'
        : '<span class="badge non">sans l&#39;offre</span>') +
      (s.statut === 'Ouvert' ? '' : ' <span class="badge ferme">' + s.statut + '</span>') + '</div>' +
      '<div class="meta">' + badgeGazole(s) +
      (s.gazole_maj ? ' <span style="font-size:.7rem">(MAJ ' + s.gazole_maj + ')</span>' : '') + '</div>';
    item.addEventListener('click', () => {
      aTirer = s.id;
      carte.setView([s.lat, s.lng], 13);
      // Si la carte ne bouge pas (déjà centrée), aucun recalcul n'aura lieu :
      // on ouvre l'infobulle directement après un court délai.
      setTimeout(() => {
        if (aTirer === s.id) {
          aTirer = null;
          marqueur.openPopup();
        }
      }, 400);
    });
    fragment.appendChild(item);
  });

  conteneur.appendChild(fragment);

  // Ouverture demandée par un clic dans la liste, après recalcul de la carte
  if (aTirer !== null) {
    const voulu = marqueurs.get(aTirer);
    aTirer = null;
    if (voulu) voulu.openPopup();
  }
}

document.querySelectorAll('#segAvantage button').forEach(bouton => {
  bouton.addEventListener('click', () => {
    filtreAvantage = bouton.dataset.valeur;
    document.querySelectorAll('#segAvantage button').forEach(b =>
      b.setAttribute('aria-pressed', String(b === bouton)));
    afficher();
  });
});
['enseigne', 'departement', 'tri', 'masquerFermes', 'clubRequis', 'recherche', 'gazole', 'prixMax', 'limite'].forEach(id =>
  document.getElementById(id).addEventListener('input', afficher));

// Le classement par distance suit le déplacement de la carte
carte.on('moveend', () => {
  centre = carte.getCenter();
  if (modeDistance) afficher();
});

// « Autour de moi » : recentre la carte sur la position du téléphone.
// La géolocalisation du navigateur exige HTTPS (GitHub Pages) ou localhost ;
// sur un accès en http:// via l'IP locale, le navigateur la refuse.
let marqueurMoi = null;

document.getElementById('geoloc').addEventListener('click', () => {
  const bouton = document.getElementById('geoloc');
  if (!navigator.geolocation) {
    alert('Géolocalisation non disponible dans ce navigateur.');
    return;
  }
  bouton.textContent = 'Localisation…';
  navigator.geolocation.getCurrentPosition(
    position => {
      const lat = position.coords.latitude, lng = position.coords.longitude;
      if (marqueurMoi) carte.removeLayer(marqueurMoi);
      marqueurMoi = L.circleMarker([lat, lng], {
        radius: 7, color: '#fff', weight: 2, fillColor: '#1a73e8', fillOpacity: 1
      }).bindPopup('Ma position').addTo(carte);
      carte.setView([lat, lng], 11);
      bouton.textContent = 'Autour de moi';
      // Les stations affichées sont celles qui nous intéressent : on vérifie
      // leur disponibilité réelle tout de suite.
      verifierEnDirect();
    },
    erreur => {
      bouton.textContent = 'Autour de moi';
      alert('Position indisponible : ' + erreur.message +
            '\n(la géolocalisation nécessite HTTPS : utilise l\'adresse github.io, pas l\'IP locale)');
    },
    { enableHighAccuracy: true, timeout: 12000 }
  );
});

// « Vérifier en direct » : interroge le jeu de données officiel (mis à jour par
// les stations toutes les ~10 minutes) pour la disponibilité et le prix du
// gazole des stations affichées. Aucun serveur intermédiaire : l'appel part du
// navigateur, ce qui permet de contrôler avant de prendre la route.
const API_PRIX = 'https://data.economie.gouv.fr/api/explore/v2.1/catalog/datasets/'
  + 'prix-des-carburants-en-france-flux-instantane-v2/records';
const TAILLE_PAQUET = 80;

const majStatut = texte => { document.getElementById('statutDirect').textContent = texte; };

const formaterDate = iso => {
  if (!iso) return '';
  const date = new Date(iso);
  if (isNaN(date)) return iso.slice(0, 16).replace('T', ' ');
  return date.toLocaleString('fr-FR', { day: '2-digit', month: '2-digit', year: 'numeric',
                                       hour: '2-digit', minute: '2-digit' });
};

function etatGazole(brut) {
  const disponibles = brut.carburants_disponibles || [];
  if (disponibles.includes('Gazole')) return 'Oui';
  if (brut.gazole_rupture_type || (brut.carburants_indisponibles || []).includes('Gazole')) return 'Non';
  return 'Non (non distribué)';
}

async function verifierEnDirect() {
  const bouton = document.getElementById('enDirect');
  const stations = stationsFiltrees().filter(s => s.gazole_officiel);
  if (!stations.length) {
    majStatut('Aucune station affichée ne publie ses prix officiels.');
    return;
  }
  bouton.disabled = true;
  bouton.textContent = 'Vérification…';
  const parId = new Map(stations.map(s => [String(s.gazole_officiel), s]));
  const ids = [...parId.keys()];
  let lues = 0, ruptures = 0;
  try {
    for (let i = 0; i < ids.length; i += TAILLE_PAQUET) {
      const paquet = ids.slice(i, i + TAILLE_PAQUET);
      const url = API_PRIX + '?limit=100&order_by=id'
        + '&select=id,gazole_prix,gazole_maj,gazole_rupture_type,carburants_disponibles,carburants_indisponibles'
        + '&where=' + encodeURIComponent('id in (' + paquet.join(',') + ')');
      const reponse = await fetch(url);
      if (!reponse.ok) throw new Error('HTTP ' + reponse.status);
      const donnees = await reponse.json();
      for (const brut of donnees.results || []) {
        const station = parId.get(String(brut.id));
        if (!station) continue;
        lues += 1;
        station.gazole_dispo = etatGazole(brut);
        station.gazole_prix = brut.gazole_prix;
        station.gazole_maj = formaterDate(brut.gazole_maj);
        station.gazole_rupture = (brut.gazole_rupture_type || '').replace(/^./, c => c.toUpperCase());
        if (station.gazole_dispo !== 'Oui') ruptures += 1;
      }
    }
    afficher();
    const heure = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    majStatut('Vérifié en direct à ' + heure + ' : ' + lues + ' station(s) actualisée(s), '
              + ruptures + ' sans gazole.');
  } catch (erreur) {
    majStatut('Vérification impossible (' + erreur.message + ') — réessaie plus tard.');
  } finally {
    bouton.disabled = false;
    bouton.textContent = 'Vérifier en direct';
  }
}

document.getElementById('enDirect').addEventListener('click', verifierEnDirect);

afficher();
</script>
</body>
</html>
"""


def build_geojson(records: list[dict]) -> list[dict]:
    """Ne garde que les champs utiles à la carte, avec un id court."""
    champs = ("nom", "enseigne", "adresse", "code_postal", "ville", "departement",
              "statut", "avantage_carburant", "club", "ouvert_2424",
              "gazole_dispo", "gazole_prix", "gazole_maj", "gazole_rupture",
              "gazole_officiel", "lat", "lng", "id")
    return [{cle: record[cle] for cle in champs} for record in records]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true", help="retélécharger les données")
    parser.add_argument("--sans-prix", action="store_true", help="ne pas afficher les prix gazole")
    parser.add_argument("--data-dir", default="data", help="dossier des données (défaut : data)")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    raw_path = data_dir / "stations.json"

    if args.refresh or not raw_path.exists():
        features = fs.fetch_all_stores()
        data_dir.mkdir(parents=True, exist_ok=True)
        raw_path.write_text(json.dumps(features, ensure_ascii=False), encoding="utf-8")
    else:
        features = json.loads(raw_path.read_text(encoding="utf-8"))

    records = [fs.to_record(f) for f in features]
    france = fs.dedupliquer([r for r in records
                             if r["pays"] == "FR" and fs.est_station_service(r)])
    france.sort(key=lambda r: (r["departement"] or "999", r["ville"], r["nom"]))

    if args.sans_prix:
        for record in france:
            record.update(gazole_dispo="Inconnu", gazole_prix=None, gazole_maj="",
                          gazole_rupture="", gazole_officiel=None)
    else:
        prix_carburants.enrichir(france, prix_carburants.telecharger(data_dir, refresh=args.refresh))

    date = fs.maintenant().strftime("%d/%m/%Y à %H:%M")
    html = (
        HTML_TEMPLATE
        .replace("__DATA__", json.dumps(build_geojson(france), ensure_ascii=False).replace("</", "<\\/"))
        .replace("__DATE__", date)
        .replace("__TOTAL__", str(len(france)))
    )

    sortie = data_dir / "carte.html"
    sortie.write_text(html, encoding="utf-8")
    print(f"Écrit : {sortie} ({len(france)} stations, dont "
          f"{sum(1 for r in france if r['avantage_carburant'])} avec l'Avantage Carburant)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
