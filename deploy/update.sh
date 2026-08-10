#!/usr/bin/env bash
# ── ARRA ADMIN — script de mise à jour ───────────────────────────────────────
# Appelé par /root/arra-admin/deploy.sh (lui-même déclenché par GitHub Actions
# ou lancé à la main). Versionné : toute modification arrive par git pull.
#
# Cible du rebuild passée en 1er argument : api | web | tout   (défaut : tout)
#
# ⚠️ RÈGLES ABSOLUES (plateforme de recrutement en production sur le même VPS) :
#    - jamais de `docker system prune`, `volume prune` ni `down -v`
#    - toujours le projet « arra-admin » et le dossier ~/arra-admin
#    - on ne touche JAMAIS à ~/arra-rh, ses conteneurs, ses volumes ni nginx

set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RACINE="/root/arra-admin"
PROJET="arra-admin"
CIBLE="${1:-tout}"

cd "$RACINE"

echo "════════════════════════════════════════════════════════"
echo "  Déploiement ARRA ADMIN — cible : $CIBLE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════"

echo
echo "── 1/5 · Récupération du code ──"
git -C backend pull --ff-only
git -C frontend pull --ff-only

echo
echo "── 2/5 · Synchronisation du docker-compose ──"
# Le compose est versionné dans le dépôt backend : on reprend toujours sa version.
cp -f backend/deploy/docker-compose.yml "$RACINE/docker-compose.yml"

echo
echo "── 3/5 · Reconstruction ──"
case "$CIBLE" in
  api)  docker compose -p "$PROJET" up -d --build api ;;
  web)  docker compose -p "$PROJET" up -d --build web ;;
  *)    docker compose -p "$PROJET" up -d --build ;;
esac

echo
echo "── 4/5 · Vérification ARRA ADMIN ──"
sleep 8
docker compose -p "$PROJET" ps
echo
curl -fsS -o /dev/null -w "  API      → HTTP %{http_code}\n" http://127.0.0.1:8011/ \
  || { echo "  ❌ L'API ne répond pas — consultez : docker compose -p $PROJET logs api"; exit 1; }
curl -fsS -o /dev/null -w "  Frontend → HTTP %{http_code}\n" http://127.0.0.1:8010/ \
  || { echo "  ❌ Le frontend ne répond pas — consultez : docker compose -p $PROJET logs web"; exit 1; }

echo
echo "── 5/5 · Non-régression : plateforme de recrutement ──"
curl -fsS -o /dev/null -w "  rh.arra-engineering.com → HTTP %{http_code}\n" https://rh.arra-engineering.com \
  || echo "  ⚠️  rh.arra-engineering.com ne répond pas (à vérifier — sans lien avec ce déploiement)"

echo
echo "✅ Déploiement terminé avec succès."
