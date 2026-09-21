#!/usr/bin/env bash
# ── ARRA ADMIN — sauvegarde PostgreSQL vers Nextcloud ────────────────────────
#
# Produit DEUX pièces par exécution, déposées sur Nextcloud dans un dossier à
# accès restreint, avec rotation sur 7 jours (comme le recrutement) :
#
#   arra-admin_AAAAMMJJ-HHMMSS.sql.gz            la base PostgreSQL
#   arra-admin_AAAAMMJJ-HHMMSS_fichiers.tar.gz   le volume `uploads`
#
# ⚠️ Les deux portent le MÊME horodatage : le dump référence des chemins de
# fichiers, les restaurer par paire évite une base qui pointe dans le vide.
#
# ⚠️ Pourquoi le volume `uploads` : il porte la **signature et le cachet
# scannés**. Ce sont des originaux numérisés — perdus, ils ne se reconstruisent
# pas (il faudrait retrouver le tampon physique et le rescanner), et sans eux
# TOUS les documents générés sortent amputés. Aucune restauration de base ne
# répare ça.
#
# ⚠️ RÈGLES ABSOLUES (plateforme de recrutement en production sur le même VPS) :
#    - on ne touche QU'AU projet « arra-admin » et à son conteneur `db`
#    - jamais MySQL, jamais ~/arra-rh, jamais un volume qui ne nous appartient pas
#    - jamais de `docker system prune`, `volume prune` ni `down -v`
#
# ⚠️ HORAIRE ET FUSEAU — à lire avant de modifier le cron.
#    La sauvegarde MySQL du recrutement tourne à **02:00 UTC**, codée en dur
#    dans l'application backend du recrutement (une tâche asyncio lancée au
#    démarrage — elle n'apparaît donc dans AUCUN cron système, ne la cherchez
#    pas là). Le serveur étant en **CEST (UTC+2)**, cela tombe à 04:00 locales.
#    Un cron s'exécute à l'heure LOCALE : ce script est donc planifié à
#    **05:00 locales = 03:00 UTC**, soit une heure après que MySQL a fini.
#    Les deux dumps ne doivent pas se chevaucher : la RAM (8 Go) est partagée,
#    et l'OOM-killer choisirait le plus gros processus de la machine — MySQL.
#
# Installation :
#    cp ~/arra-admin/backend/deploy/backup.sh /root/arra-admin/backup.sh
#    chmod +x /root/arra-admin/backup.sh
#    puis le cron — voir deploy/SAUVEGARDES.md

set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

RACINE="/root/arra-admin"
PROJET="arra-admin"
LOCAL="$RACINE/sauvegardes"      # copie de secours sur le disque du VPS
RETENTION_LOCALE=3               # moins qu'à distance : le VPS a peu de place
JOURNAL="/var/log/arra-admin-backup.log"

cd "$RACINE"

# ── Verrou : jamais deux dumps en parallèle ──────────────────────────────────
# Le cron et un lancement manuel peuvent se croiser. Deux `pg_dump` simultanés
# doubleraient la mémoire consommée, ce qui est précisément le risque à éviter.
exec 9>/var/lock/arra-admin-backup.lock
if ! flock -n 9; then
  echo "⏭  Une sauvegarde est déjà en cours — abandon." >&2
  exit 0
fi

# ── Configuration ────────────────────────────────────────────────────────────
# POSTGRES_USER / POSTGRES_DB viennent du .env utilisé par docker compose.
if [ ! -f "$RACINE/.env" ]; then
  echo "❌ $RACINE/.env introuvable." >&2
  exit 1
fi
set -a; . "$RACINE/.env"; set +a
: "${POSTGRES_USER:?POSTGRES_USER absent du .env}"
: "${POSTGRES_DB:?POSTGRES_DB absent du .env}"

compose() { docker compose -p "$PROJET" "$@"; }

echo "════════════════════════════════════════════════════════"
echo "  Sauvegarde ARRA ADMIN"
echo "  $(date '+%Y-%m-%d %H:%M:%S %Z')  ·  $(date -u '+%H:%M UTC')"
echo "════════════════════════════════════════════════════════"

# ── 1/6 · La base répond-elle ? ──────────────────────────────────────────────
echo
echo "── 1/6 · Vérification de la base ──"
if ! compose exec -T db pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
  echo "❌ Le conteneur db du projet $PROJET ne répond pas — sauvegarde annulée." >&2
  exit 1
fi
echo "  base joignable : $POSTGRES_DB"

# ── 2/6 · Dump ───────────────────────────────────────────────────────────────
echo
echo "── 2/6 · Dump ──"
mkdir -p "$LOCAL"
# ⚠️ Les deux noms sont dérivés du MÊME appel : les demander séparément ferait
# diverger l'horodatage si l'exécution chevauche une seconde, et la paire ne se
# retrouverait plus.
NOM="$(compose exec -T api python -m app.sauvegarde nom | tr -d '\r\n')"
if [ -z "$NOM" ]; then
  echo "❌ Impossible de déterminer le nom de la sauvegarde." >&2
  exit 1
fi
HORODATAGE="${NOM#arra-admin_}"; HORODATAGE="${HORODATAGE%.sql.gz}"
NOM_FICHIERS="arra-admin_${HORODATAGE}_fichiers.tar.gz"
CIBLE="$LOCAL/$NOM"
CIBLE_FICHIERS="$LOCAL/$NOM_FICHIERS"

# --clean --if-exists : le dump sait se réappliquer sur une base non vide.
# Sans cela, une restauration exigerait de supprimer la base à la main.
# ⚠️ `exec -T` sans `-i` : pas de TTY, sinon le flux binaire serait corrompu.
set +e
compose exec -T db pg_dump \
    -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --clean --if-exists --no-owner --no-privileges \
  | gzip -9 > "$CIBLE"
CODES=("${PIPESTATUS[@]}")
set -e
if [ "${CODES[0]}" -ne 0 ] || [ "${CODES[1]}" -ne 0 ]; then
  echo "❌ pg_dump a échoué (codes : ${CODES[*]}) — fichier incomplet supprimé." >&2
  rm -f "$CIBLE"
  exit 1
fi

# ── 3/6 · Le dump est-il valide ? ────────────────────────────────────────────
# ⚠️ Contrôler AVANT d'envoyer : déposer un fichier tronqué déclencherait la
# rotation et ferait tomber une sauvegarde saine de la fenêtre des 7 jours.
echo
echo "── 3/6 · Contrôle du dump ──"
if ! gzip -t "$CIBLE" 2>/dev/null; then
  echo "❌ Archive gzip corrompue — envoi annulé." >&2
  rm -f "$CIBLE"
  exit 1
fi
OCTETS=$(stat -c%s "$CIBLE")
if [ "$OCTETS" -lt 1024 ]; then
  echo "❌ Dump suspect ($OCTETS octets) — envoi annulé." >&2
  rm -f "$CIBLE"
  exit 1
fi
# Un dump valide contient forcément la ligne d'en-tête de pg_dump.
if ! gzip -dc "$CIBLE" | head -c 4096 | grep -q "PostgreSQL database dump"; then
  echo "❌ Le contenu n'est pas un dump PostgreSQL — envoi annulé." >&2
  rm -f "$CIBLE"
  exit 1
fi
echo "  $NOM  ·  $(numfmt --to=iec --suffix=o "$OCTETS" 2>/dev/null || echo "$OCTETS o")"

# ── 4/6 · Archive du volume uploads ──────────────────────────────────────────
echo
echo "── 4/6 · Archive des fichiers déposés ──"
set +e
compose exec -T api python -m app.sauvegarde archiver-fichiers > "$CIBLE_FICHIERS"
CODE_TAR=$?
set -e
if [ "$CODE_TAR" -ne 0 ] || ! gzip -t "$CIBLE_FICHIERS" 2>/dev/null; then
  # ⚠️ NON bloquant : perdre l'archive des fichiers est grave, mais renoncer
  # AUSSI au dump de la base le serait davantage. On poursuit en le disant.
  echo "  ⚠️  Archive des fichiers échouée — la base sera sauvegardée seule." >&2
  rm -f "$CIBLE_FICHIERS"
  CIBLE_FICHIERS=""
else
  echo "  $NOM_FICHIERS  ·  $(numfmt --to=iec --suffix=o "$(stat -c%s "$CIBLE_FICHIERS")" 2>/dev/null)"
fi

# ── 5/6 · Dépôt sur Nextcloud + rotation ─────────────────────────────────────
# Le mot de passe Nextcloud est chiffré en base : seul le conteneur `api` sait
# le déchiffrer. On lui pousse le fichier sur stdin plutôt que de recopier le
# secret dans ce script.
echo
echo "── 5/6 · Dépôt sur Nextcloud ──"
# ⚠️ Les FICHIERS d'abord, la BASE ensuite. C'est le dépôt qui déclenche la
# rotation : dans l'autre ordre, elle pourrait purger une ancienne sauvegarde
# alors que la paire courante n'est pas encore complète.
if [ -n "$CIBLE_FICHIERS" ]; then
  if ! compose exec -T api python -m app.sauvegarde deposer --nom "$NOM_FICHIERS" < "$CIBLE_FICHIERS"; then
    echo "  ⚠️  Dépôt de l'archive des fichiers échoué — copie locale : $CIBLE_FICHIERS" >&2
  fi
fi

if ! compose exec -T api python -m app.sauvegarde deposer --nom "$NOM" < "$CIBLE"; then
  echo "⚠️  Dépôt distant échoué — la copie locale est conservée : $CIBLE" >&2
  echo "    (relancer ce script, ou déposer le fichier à la main)" >&2
  exit 1
fi

# ── 6/6 · Rétention locale ───────────────────────────────────────────────────
echo
echo "── 6/6 · Rétention locale ($RETENTION_LOCALE sauvegardes) ──"
# ⚠️ Motif strict : ce `rm` ne doit jamais pouvoir toucher autre chose que nos
# propres sauvegardes, même si quelqu'un dépose un fichier dans ce dossier.
#
# ⚠️ On compte des SAUVEGARDES, pas des fichiers : chacune en pèse deux depuis
# l'ajout de l'archive des fichiers. Garder « les 3 derniers fichiers » ne
# garderait plus qu'une journée et demie.
MOTIF_LOCAL='.*/arra-admin_[0-9]{8}-[0-9]{6}(\.sql\.gz|_fichiers\.tar\.gz)'

mapfile -t GARDES < <(
  find "$LOCAL" -maxdepth 1 -type f -regextype posix-extended -regex "$MOTIF_LOCAL" \
    | sed -E 's/.*arra-admin_([0-9]{8}-[0-9]{6}).*/\1/' \
    | sort -ru | head -n "$RETENTION_LOCALE"
)
while IFS= read -r f; do
  [ -n "$f" ] || continue
  H=$(basename "$f" | sed -E 's/arra-admin_([0-9]{8}-[0-9]{6}).*/\1/')
  if ! printf '%s\n' "${GARDES[@]:-}" | grep -qx "$H"; then
    rm -f "$f" && echo "  purgé : $(basename "$f")"
  fi
done < <(find "$LOCAL" -maxdepth 1 -type f -regextype posix-extended -regex "$MOTIF_LOCAL")

echo
echo "✅ Sauvegarde terminée."
echo "   Journal : $JOURNAL"
