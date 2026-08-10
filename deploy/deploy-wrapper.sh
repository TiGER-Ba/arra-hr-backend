#!/usr/bin/env bash
# ── Point d'entrée du déploiement (à installer en /root/arra-admin/deploy.sh) ─
#
# C'est la SEULE commande que la clé SSH de GitHub Actions peut exécuter
# (« forced command » dans ~/.ssh/authorized_keys). Même si cette clé fuitait,
# elle ne donnerait ni shell, ni lecture de fichiers, ni accès au recrutement.
#
# Ce fichier est volontairement STABLE : il récupère d'abord la dernière version
# du dépôt backend, puis passe la main au script versionné update.sh. Sans cela,
# bash exécuterait un script en train d'être modifié sous ses pieds.
#
# Installation (une seule fois) :
#   cp ~/arra-admin/backend/deploy/deploy-wrapper.sh /root/arra-admin/deploy.sh
#   chmod +x /root/arra-admin/deploy.sh

set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# La cible arrive via SSH_ORIGINAL_COMMAND (ce que le client a tenté de lancer).
# ⚠️ Entrée NON FIABLE : on n'accepte QUE trois valeurs, rien n'est interprété.
BRUT="${SSH_ORIGINAL_COMMAND:-tout}"
case "$BRUT" in
  api)  CIBLE="api"  ;;
  web)  CIBLE="web"  ;;
  *)    CIBLE="tout" ;;
esac

cd /root/arra-admin

# Met à jour le dépôt backend (qui contient update.sh) AVANT de l'exécuter.
git -C backend pull --ff-only >/dev/null 2>&1 || true

exec bash /root/arra-admin/backend/deploy/update.sh "$CIBLE"
