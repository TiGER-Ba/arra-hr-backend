#!/usr/bin/env bash
# ── ARRA ADMIN — restauration d'une sauvegarde PostgreSQL ───────────────────
#
# ⚠️ CE SCRIPT ÉCRASE LA BASE DE LA PLATEFORME RH. Il demande une confirmation
# explicite et prend une sauvegarde de sécurité avant d'agir.
#
# ⚠️ Il ne touche QU'AU projet « arra-admin ». Jamais MySQL, jamais ~/arra-rh.
#
# Usage :
#   ./restaurer.sh --lister                       voir ce qui est disponible
#   ./restaurer.sh --nextcloud arra-admin_2026….sql.gz
#   ./restaurer.sh --fichier /root/arra-admin/sauvegardes/arra-admin_….sql.gz
#
# Une sauvegarde jamais restaurée n'est pas une sauvegarde : faites le test au
# moins une fois, sur une base de test, AVANT d'en avoir besoin.
# Procédure complète : deploy/SAUVEGARDES.md

set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RACINE="/root/arra-admin"
PROJET="arra-admin"
LOCAL="$RACINE/sauvegardes"

cd "$RACINE"
compose() { docker compose -p "$PROJET" "$@"; }

SOURCE=""
NOM_DISTANT=""
FICHIER=""

while [ $# -gt 0 ]; do
  case "$1" in
    --lister)    SOURCE="lister"; shift ;;
    --nextcloud) SOURCE="distant"; NOM_DISTANT="${2:-}"; shift 2 ;;
    --fichier)   SOURCE="local";   FICHIER="${2:-}";     shift 2 ;;
    *) echo "Option inconnue : $1" >&2; exit 1 ;;
  esac
done

if [ "$SOURCE" = "lister" ] || [ -z "$SOURCE" ]; then
  echo "── Sauvegardes sur Nextcloud ──"
  compose exec -T api python -m app.sauvegarde lister || true
  echo
  echo "── Copies locales ($LOCAL) ──"
  ls -lh "$LOCAL"/arra-admin_*.sql.gz 2>/dev/null || echo "  (aucune)"
  [ -z "$SOURCE" ] && { echo; echo "Indiquez --nextcloud <nom> ou --fichier <chemin>."; }
  exit 0
fi

set -a; . "$RACINE/.env"; set +a
: "${POSTGRES_USER:?POSTGRES_USER absent du .env}"
: "${POSTGRES_DB:?POSTGRES_DB absent du .env}"

# ── Récupération de la sauvegarde à restaurer ────────────────────────────────
TEMP="$(mktemp -d)"
trap 'rm -rf "$TEMP"' EXIT

if [ "$SOURCE" = "distant" ]; then
  [ -n "$NOM_DISTANT" ] || { echo "❌ Nom manquant." >&2; exit 1; }
  echo "── Téléchargement depuis Nextcloud : $NOM_DISTANT ──"
  compose exec -T api python -m app.sauvegarde recuperer --nom "$NOM_DISTANT" > "$TEMP/dump.sql.gz"
  A_RESTAURER="$TEMP/dump.sql.gz"
else
  [ -f "$FICHIER" ] || { echo "❌ Fichier introuvable : $FICHIER" >&2; exit 1; }
  A_RESTAURER="$FICHIER"
fi

# ── Contrôle AVANT de toucher à la base ──────────────────────────────────────
echo
echo "── Contrôle de l'archive ──"
gzip -t "$A_RESTAURER" || { echo "❌ Archive corrompue — restauration annulée." >&2; exit 1; }
gzip -dc "$A_RESTAURER" | head -c 4096 | grep -q "PostgreSQL database dump" \
  || { echo "❌ Ce n'est pas un dump PostgreSQL — restauration annulée." >&2; exit 1; }
echo "  archive valide · $(numfmt --to=iec --suffix=o "$(stat -c%s "$A_RESTAURER")" 2>/dev/null)"

# ── Confirmation explicite ───────────────────────────────────────────────────
echo
echo "⚠️  La base « $POSTGRES_DB » du projet $PROJET va être ÉCRASÉE."
echo "    Tout ce qui y a été saisi depuis cette sauvegarde sera perdu."
printf "    Tapez exactement RESTAURER pour continuer : "
read -r REPONSE
[ "$REPONSE" = "RESTAURER" ] || { echo "Annulé."; exit 0; }

# ── Filet : sauvegarde de l'état actuel avant de l'écraser ───────────────────
# Si la restauration se révèle être une erreur, il faut pouvoir revenir.
echo
echo "── Sauvegarde de sécurité de l'état actuel ──"
mkdir -p "$LOCAL"
AVANT="$LOCAL/avant-restauration_$(date -u '+%Y%m%d-%H%M%S').sql.gz"
if compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
     --clean --if-exists --no-owner --no-privileges | gzip -9 > "$AVANT"; then
  echo "  $AVANT"
else
  rm -f "$AVANT"
  echo "⚠️  Impossible de sauvegarder l'état actuel."
  printf "    Continuer QUAND MÊME ? (oui/non) : "
  read -r SUITE
  [ "$SUITE" = "oui" ] || { echo "Annulé."; exit 0; }
fi

# ── Restauration ─────────────────────────────────────────────────────────────
# L'API est arrêtée le temps de l'opération : des écritures concurrentes
# pendant un DROP/CREATE laisseraient la base dans un état incohérent.
# ⚠️ `stop`, pas `down` — `down` toucherait au réseau et aux volumes.
echo
echo "── Arrêt de l'API ──"
compose stop api web

echo
echo "── Restauration ──"
if gzip -dc "$A_RESTAURER" | compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 >/dev/null; then
  echo "  base restaurée"
  ETAT=0
else
  echo "❌ La restauration a échoué." >&2
  echo "   L'état précédent est dans : $AVANT" >&2
  ETAT=1
fi

echo
echo "── Redémarrage ──"
compose start api web
sleep 8
curl -fsS -o /dev/null -w "  API      → HTTP %{http_code}\n" http://127.0.0.1:8011/ || echo "  ⚠️ API muette"
curl -fsS -o /dev/null -w "  Frontend → HTTP %{http_code}\n" http://127.0.0.1:8010/ || echo "  ⚠️ Frontend muet"

echo
echo "── Non-régression : plateforme de recrutement ──"
curl -fsS -o /dev/null -w "  rh.arra-engineering.com → HTTP %{http_code}\n" https://rh.arra-engineering.com \
  || echo "  ⚠️ rh.arra-engineering.com ne répond pas (sans lien avec cette opération)"

exit $ETAT
