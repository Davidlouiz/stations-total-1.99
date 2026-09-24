#!/usr/bin/env bash
# Installe les unités systemd utilisateur du projet :
#   - stations-total-refresh.timer  : rafraîchit les données chaque matin
#   - stations-total-web.service    : sert la carte sur http://localhost:8000
#
# Usage :
#   ./install_services.sh              installe tout et active
#   ./install_services.sh --no-web     seulement le rafraîchissement automatique
#
# Désinstallation :
#   systemctl --user disable --now stations-total-refresh.timer stations-total-web.service
#   rm ~/.config/systemd/user/stations-total-*.{service,timer}

set -euo pipefail

PROJET="$(cd "$(dirname "$0")" && pwd)"
CIBLE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(stations-total-refresh.service stations-total-refresh.timer stations-total-web.service)

avec_web=1
[[ "${1:-}" == "--no-web" ]] && avec_web=0

mkdir -p "$CIBLE"
for unite in "${UNITS[@]}"; do
  sed "s|%PROJET%|${PROJET}|g" "${PROJET}/systemd/${unite}" > "${CIBLE}/${unite}"
  echo "installé : ${CIBLE}/${unite}"
done

systemctl --user daemon-reload
systemctl --user enable --now stations-total-refresh.timer
echo "activé   : stations-total-refresh.timer"

if [[ $avec_web -eq 1 ]]; then
  systemctl --user enable --now stations-total-web.service
  echo "activé   : stations-total-web.service (http://localhost:8000/data/carte.html)"
else
  echo "serveur web non activé (--no-web)"
fi

echo
systemctl --user list-timers stations-total-refresh.timer --no-pager
echo
echo "État du rafraîchissement : systemctl --user status stations-total-refresh.service"
echo "Journal                  : journalctl --user -u stations-total-refresh.service -n 30"
echo "Lancer maintenant        : systemctl --user start stations-total-refresh.service"
