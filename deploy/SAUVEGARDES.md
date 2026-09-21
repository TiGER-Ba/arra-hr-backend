# Sauvegardes PostgreSQL — ARRA Admin RH

> ⚠️ **Avant ce dispositif, la base n'était sauvegardée nulle part.** Les
> comptes, salaires, CIN, RIB, pointages, soldes et l'index des documents
> Nextcloud vivaient dans un seul volume Docker, sur un seul disque.

## Ce qui tourne

| | |
|---|---|
| Quoi | `pg_dump` de la base `arra-admin`, compressé gzip |
| Où | Nextcloud → **`6.11 Sauvegardes Admin RH`** |
| Quand | **05:00 heure serveur (CEST) = 03:00 UTC**, tous les jours |
| Rétention | **7** sur Nextcloud, **3** en copie locale sur le VPS |
| Avant chaque déploiement | un dump supplémentaire (`update.sh`, étape 3/6) |

## 🔴 À FAIRE CÔTÉ NEXTCLOUD — restreindre l'accès au dossier

**Le dump contient les salaires, les CIN et les RIB en clair.**

Le dossier `6.11 Sauvegardes Admin RH` doit être accessible **au seul
administrateur**, jamais à l'équipe RH qui parcourt `6.10 RH Admin web` au
quotidien. Un dump ouvert à tous serait pire que pas de sauvegarde : il
transformerait une protection en fuite.

À vérifier dans Nextcloud : le dossier n'est partagé avec **aucun** groupe, et
aucun lien public n'existe dessus.

## Pourquoi 05:00 et pas une autre heure

⚠️ **Le piège du fuseau.** La sauvegarde MySQL du **recrutement** tourne à
**02:00 UTC**. Elle n'est pas dans un cron système — elle est programmée
*dans l'application backend du recrutement*, par une tâche asyncio lancée au
démarrage. Inutile de la chercher dans `crontab -l` ou `systemctl list-timers`.

Le serveur est en **CEST (UTC+2)**, donc MySQL s'exécute à **04:00 locales**.
Un cron s'exécute à l'heure **locale** : écrire `30 3 * * *` en croyant viser
03:30 UTC le placerait en réalité à **01:30 UTC**, soit *avant* MySQL.

D'où **05:00 locales** — une heure après que MySQL a terminé, et bien avant la
journée de travail.

⚠️ Les deux dumps ne doivent jamais se chevaucher : la RAM (8 Go) est partagée
entre les deux plateformes, et l'OOM-killer du noyau choisirait le plus gros
processus de la machine, c'est-à-dire **MySQL**.

## Installation (une seule fois)

```bash
cp ~/arra-admin/backend/deploy/backup.sh    /root/arra-admin/backup.sh
cp ~/arra-admin/backend/deploy/restaurer.sh /root/arra-admin/restaurer.sh
chmod +x /root/arra-admin/backup.sh /root/arra-admin/restaurer.sh
```

Puis le cron :

```bash
sudo tee /etc/cron.d/arra-admin-backup >/dev/null <<'CRON'
# Sauvegarde PostgreSQL ARRA Admin RH.
# ⚠️ HEURE LOCALE (serveur en CEST, UTC+2) : 05:00 locales = 03:00 UTC.
# La sauvegarde MySQL du recrutement tourne à 02:00 UTC (= 04:00 locales),
# depuis l'application backend du recrutement et non depuis un cron.
# Ne pas rapprocher les deux : RAM partagée, risque d'OOM sur MySQL.
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0 5 * * * root /root/arra-admin/backup.sh >> /var/log/arra-admin-backup.log 2>&1
CRON
sudo chmod 0644 /etc/cron.d/arra-admin-backup
```

Rotation du journal :

```bash
sudo tee /etc/logrotate.d/arra-admin-backup >/dev/null <<'LOG'
/var/log/arra-admin-backup.log {
    weekly
    rotate 8
    compress
    missingok
    notifempty
}
LOG
```

## Vérifier que ça marche

```bash
# Lancer une sauvegarde à la main
/root/arra-admin/backup.sh

# Voir ce qui est sur Nextcloud
cd /root/arra-admin && docker compose -p arra-admin exec -T api \
  python -m app.sauvegarde lister

# Le lendemain : le cron a-t-il tourné ?
tail -40 /var/log/arra-admin-backup.log
```

⚠️ **À contrôler une fois par mois** : que le fichier le plus récent date bien
d'hier. Une sauvegarde qui s'arrête ne prévient personne — c'est la panne la
plus courante, et on ne la découvre qu'au moment d'en avoir besoin.

## Restaurer

```bash
# 1. Voir ce qui est disponible
/root/arra-admin/restaurer.sh --lister

# 2. Restaurer depuis Nextcloud
/root/arra-admin/restaurer.sh --nextcloud arra-admin_20260921-030000.sql.gz

# ou depuis la copie locale
/root/arra-admin/restaurer.sh --fichier /root/arra-admin/sauvegardes/arra-admin_….sql.gz
```

Le script demande de taper `RESTAURER`, prend un dump de l'état actuel avant
d'écraser (dans `sauvegardes/avant-restauration_*.sql.gz`), arrête l'API le
temps de l'opération, puis la redémarre et vérifie que le recrutement répond
toujours.

## 🔴 Tester la restauration AVANT d'en avoir besoin

**Une sauvegarde jamais restaurée n'est pas une sauvegarde.** Le test doit être
fait au moins une fois, et il ne se fait pas sur la base de production :

```bash
cd /root/arra-admin
# Base jetable dans le conteneur existant — ne touche pas à la vraie base
docker compose -p arra-admin exec -T db createdb -U "$POSTGRES_USER" test_restauration
gzip -dc sauvegardes/arra-admin_….sql.gz | \
  docker compose -p arra-admin exec -T db psql -U "$POSTGRES_USER" -d test_restauration -v ON_ERROR_STOP=1

# Les données sont-elles là ?
docker compose -p arra-admin exec -T db psql -U "$POSTGRES_USER" -d test_restauration \
  -c "SELECT count(*) AS utilisateurs FROM utilisateurs;" \
  -c "SELECT count(*) AS employes FROM employes;" \
  -c "SELECT count(*) AS pointages FROM pointages;"

# Ménage
docker compose -p arra-admin exec -T db dropdb -U "$POSTGRES_USER" test_restauration
```

## Ce que la sauvegarde ne couvre PAS

| | Où c'est | Protection |
|---|---|---|
| Documents du personnel | Nextcloud `6.10 RH Admin web` | sauvegarde Nextcloud de l'entreprise |
| Signature / cachet, pièces déposées avant Nextcloud | volume `uploads` | ⚠️ **aucune** |
| Index RAG (base de connaissances) | volume `chroma` | reconstructible depuis `/rh/knowledge` |

⚠️ Le volume `uploads` n'est pas sauvegardé. Il ne contient plus grand-chose
depuis la bascule sur Nextcloud, mais il porte encore la **signature et le
cachet scannés** utilisés par tous les documents générés. À traiter si vous
voulez une couverture complète.

## Isolement — ce que ces scripts ne font jamais

- ils n'utilisent QUE `docker compose -p arra-admin`, jamais un conteneur du
  recrutement ;
- ils ne touchent ni à MySQL, ni à `~/arra-rh`, ni à un volume qui ne leur
  appartient pas ;
- aucun `docker system prune`, `volume prune` ni `down -v` ;
- la rotation ne supprime **que** les fichiers correspondant exactement à
  `arra-admin_AAAAMMJJ-HHMMSS.sql.gz`, localement comme sur Nextcloud. Un
  fichier déposé à la main dans le dossier n'est jamais touché ;
- `restaurer.sh` arrête l'API avec `stop`, jamais `down` — `down` toucherait au
  réseau et aux volumes.
