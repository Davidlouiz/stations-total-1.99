#!/usr/bin/env python3
"""Affiche un résumé des données générées (et l'ajoute au résumé d'un job
GitHub Actions quand la variable GITHUB_STEP_SUMMARY est présente)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    data_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "data")
    meta = json.loads((data_dir / "meta.json").read_text(encoding="utf-8"))
    prix = meta["prix_gazole"]

    lignes = [
        "### Données TotalEnergies mises à jour",
        "",
        f"- Générées le : {meta['genere_le']}",
        f"- Stations-service : {meta['total']}",
        f"- Offre Avantage Carburant : {meta['avec_offre']} "
        f"(dont {meta['avec_offre_et_club']} avec adhésion Club en station)",
        f"- Gazole disponible : {meta['gazole_disponible']}",
        f"  - prix déclarés par les stations (jeu officiel) : {meta.get('gazole_source_officiel', 0)}",
        f"  - prix plafonnés (fiches du localisateur TotalEnergies) : {meta.get('gazole_plafonne', 0)}",
        f"- Prix du gazole : {prix['min']} à {prix['max']} €/L (médian {prix['median']})",
    ]
    texte = "\n".join(lignes)

    print(texte)
    resume = os.environ.get("GITHUB_STEP_SUMMARY")
    if resume:
        with open(resume, "a", encoding="utf-8") as fichier:
            fichier.write(texte + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
