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

# ── Verrou : un seul déploiement à la fois ───────────────────────────────────
# Le backend et le frontend vivent dans DEUX dépôts distincts, qui déclenchent
# ce script indépendamment. Le « concurrency » de GitHub Actions est par dépôt :
# il ne sérialise donc RIEN entre les deux. Deux `docker compose up --build`
# lancés en parallèle sur le même projet se marchent dessus, et l'un des deux
# conteneurs n'est pas reconstruit — en silence, le workflow restant vert.
exec 9>/var/lock/arra-admin-deploy.lock
if ! flock -w 1200 9; then
  echo "❌ Un déploiement est déjà en cours depuis plus de 20 min — abandon."
  exit 1
fi

echo "════════════════════════════════════════════════════════"
echo "  Déploiement ARRA ADMIN — cible : $CIBLE"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════════════════════"

echo
echo "── 1/5 · Récupération du code ──"
AVANT_API=$(git -C backend rev-parse HEAD)
AVANT_WEB=$(git -C frontend rev-parse HEAD)
git -C backend pull --ff-only
git -C frontend pull --ff-only
APRES_API=$(git -C backend rev-parse HEAD)
APRES_WEB=$(git -C frontend rev-parse HEAD)

echo
echo "── 2/5 · Synchronisation du docker-compose ──"
# Le compose est versionné dans le dépôt backend : on reprend toujours sa version.
cp -f backend/deploy/docker-compose.yml "$RACINE/docker-compose.yml"

echo
echo "── 3/5 · Reconstruction ──"
case "$CIBLE" in
  api)  SERVICES=(api) ;;
  web)  SERVICES=(web) ;;
  *)    SERVICES=(api web) ;;
esac

# Filet de sécurité : ce script pull TOUJOURS les deux dépôts, mais ne
# reconstruit que la cible demandée. Si un dépôt a bougé sans que son conteneur
# soit visé — déploiement précédent perdu, ou code déjà pull par l'autre
# workflow — le code resterait sur le disque sans jamais être construit.
# On reconstruit donc aussi tout ce qui a réellement changé.
if [ "$AVANT_API" != "$APRES_API" ]; then SERVICES+=(api); fi
if [ "$AVANT_WEB" != "$APRES_WEB" ]; then SERVICES+=(web); fi
mapfile -t SERVICES < <(printf '%s\n' "${SERVICES[@]}" | sort -u)

echo "  services reconstruits : ${SERVICES[*]}"
docker compose -p "$PROJET" up -d --build "${SERVICES[@]}"

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
