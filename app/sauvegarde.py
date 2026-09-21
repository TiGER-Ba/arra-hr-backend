"""Dépôt et rotation des sauvegardes PostgreSQL sur Nextcloud.

Appelé par `deploy/backup.sh`, qui produit le dump et le pousse sur **l'entrée
standard** de ce module, exécuté DANS le conteneur `api` :

    docker compose -p arra-admin exec -T api python -m app.sauvegarde deposer \\
        --nom arra-admin_20260921-050000.sql.gz < dump.sql.gz

⚠️ **Pourquoi passer par le conteneur `api` plutôt que par du bash.** Le mot de
passe Nextcloud est **chiffré** en base (Fernet, clé dérivée de `SECRET_KEY`).
Un script shell ne peut pas le lire sans qu'on le recopie en clair quelque part
— ce qui annulerait le chiffrement. Ici on réutilise le client déjà écrit et
déjà testé, et le secret ne quitte jamais le processus.

⚠️ **Dossier SÉPARÉ des documents.** Le dump contient les salaires, les CIN et
les RIB **en clair**. Il ne doit pas atterrir dans le dossier que l'équipe RH
parcourt au quotidien. `RACINE_SAUVEGARDES` est distinct de la racine
documentaire, et ses droits Nextcloud doivent être **restreints à
l'administrateur** — un dump lisible par tous serait pire que pas de
sauvegarde.
"""
import argparse
import dataclasses
import re
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from app.services import nextcloud

#: Dossier Nextcloud des sauvegardes — JAMAIS celui des documents du personnel.
RACINE_DEFAUT = "6.11 Sauvegardes Admin RH"
CLE_RACINE = "nextcloud_racine_sauvegardes"

#: Nombre de sauvegardes conservées, aligné sur la rotation du recrutement.
RETENTION = 7

#: ⚠️ Motif STRICT des fichiers que la rotation s'autorise à supprimer.
#: La rotation efface des données sur un drive partagé : elle ne doit jamais
#: pouvoir toucher un fichier qu'elle n'a pas écrit elle-même. Tout ce qui ne
#: correspond pas exactement est ignoré, y compris un dump déposé à la main.
MOTIF_SAUVEGARDE = re.compile(r"^arra-admin_\d{8}-\d{6}\.sql\.gz$")

PREFIXE = "arra-admin_"
SUFFIXE = ".sql.gz"

#: En dessous, le dump est forcément tronqué ou vide — on refuse de l'envoyer
#: et surtout de faire tourner la rotation derrière.
TAILLE_MINIMALE = 1024


def nom_sauvegarde(instant: datetime | None = None) -> str:
    """« arra-admin_20260921-050000.sql.gz » — triable par ordre alphabétique.

    Horodaté en **UTC** : le serveur est en CEST, et un nom en heure locale
    reculerait d'une heure au passage à l'heure d'hiver, cassant l'ordre de
    rotation une nuit par an.
    """
    instant = instant or datetime.now(timezone.utc)
    return f"{PREFIXE}{instant.strftime('%Y%m%d-%H%M%S')}{SUFFIXE}"


def config_sauvegardes(db):
    """Config Nextcloud pointée sur le dossier des sauvegardes.

    On réutilise l'URL, l'utilisateur et le mot de passe déchiffré, mais on
    **remplace la racine**. Le garde-fou de `_chemin_sur` s'applique alors à
    `6.11 …` : ce module ne peut pas plus écrire hors de son dossier que le
    reste de l'application ne peut sortir du sien.
    """
    from app.services.parametrage import get_param

    cfg = nextcloud.config(db)          # lève NonConfigure si Nextcloud absent
    racine = (get_param(db, CLE_RACINE, "") or "").strip() or RACINE_DEFAUT
    return dataclasses.replace(cfg, racine=racine)


def deposer(contenu: bytes, nom: str) -> dict:
    """Envoie le dump, puis fait tourner la rétention. Rend un compte rendu."""
    if len(contenu) < TAILLE_MINIMALE:
        raise ValueError(
            f"Dump suspect ({len(contenu)} octets) : envoi refusé. "
            "Un fichier tronqué remplacerait une sauvegarde valide."
        )
    if not MOTIF_SAUVEGARDE.match(nom):
        raise ValueError(f"Nom de sauvegarde non conforme : {nom!r}")

    db = SessionLocal()
    try:
        cfg = config_sauvegardes(db)
        nextcloud.assurer_dossier(cfg, "")
        nextcloud.envoyer(cfg, nom, contenu, "application/gzip")
        supprimees = _rotation(cfg)
        restantes = _sauvegardes(cfg)
    finally:
        db.close()

    return {
        "depose": nom,
        "octets": len(contenu),
        "racine": cfg.racine,
        "supprimees": supprimees,
        "conservees": restantes,
    }


def _sauvegardes(cfg) -> list[str]:
    """Nos sauvegardes présentes, les plus récentes d'abord."""
    return sorted(
        (e["nom"] for e in nextcloud.lister(cfg, "")
         if not e["dossier"] and MOTIF_SAUVEGARDE.match(e["nom"])),
        reverse=True,
    )


def _rotation(cfg) -> list[str]:
    """Supprime les sauvegardes au-delà de `RETENTION`.

    ⚠️ Ne supprime QUE ce que `MOTIF_SAUVEGARDE` reconnaît. Un fichier déposé
    à la main dans ce dossier, une archive d'un autre outil, un sous-dossier :
    rien de tout cela n'est touché.
    """
    toutes = _sauvegardes(cfg)
    a_supprimer = toutes[RETENTION:]
    for nom in a_supprimer:
        nextcloud.supprimer(cfg, nom)
    return a_supprimer


def lister() -> dict:
    """Sauvegardes présentes — sert à vérifier que la rotation vit."""
    db = SessionLocal()
    try:
        cfg = config_sauvegardes(db)
        noms = _sauvegardes(cfg)
        details = {e["nom"]: e for e in nextcloud.lister(cfg, "")}
    finally:
        db.close()
    return {
        "racine": cfg.racine,
        "nombre": len(noms),
        "sauvegardes": [
            {"nom": n, "octets": details.get(n, {}).get("taille"),
             "modifie": details.get(n, {}).get("modifie")}
            for n in noms
        ],
    }


def recuperer(nom: str) -> bytes:
    """Contenu d'une sauvegarde — utilisé par `deploy/restaurer.sh`."""
    if not MOTIF_SAUVEGARDE.match(nom):
        raise ValueError(f"Nom de sauvegarde non conforme : {nom!r}")
    db = SessionLocal()
    try:
        return nextcloud.lire(config_sauvegardes(db), nom)
    finally:
        db.close()


def main(argv=None) -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    sous = parseur.add_subparsers(dest="action", required=True)

    p_dep = sous.add_parser("deposer", help="lit le dump sur stdin et l'envoie")
    p_dep.add_argument("--nom", required=True)

    sous.add_parser("lister", help="liste les sauvegardes présentes")
    sous.add_parser("nom", help="affiche le nom à utiliser pour maintenant")

    p_rec = sous.add_parser("recuperer", help="écrit une sauvegarde sur stdout")
    p_rec.add_argument("--nom", required=True)

    args = parseur.parse_args(argv)

    try:
        if args.action == "nom":
            print(nom_sauvegarde())
            return 0

        if args.action == "deposer":
            compte_rendu = deposer(sys.stdin.buffer.read(), args.nom)
            mo = compte_rendu["octets"] / 1048576
            print(f"  déposé   : {compte_rendu['depose']} ({mo:.1f} Mo)")
            print(f"  dossier  : {compte_rendu['racine']}")
            print(f"  conservées : {len(compte_rendu['conservees'])}")
            for nom in compte_rendu["supprimees"]:
                print(f"  purgée   : {nom}")
            return 0

        if args.action == "lister":
            etat = lister()
            print(f"  dossier : {etat['racine']}")
            print(f"  nombre  : {etat['nombre']}")
            for s in etat["sauvegardes"]:
                mo = (s["octets"] or 0) / 1048576
                print(f"    {s['nom']}  {mo:6.1f} Mo  {s['modifie'] or ''}")
            return 0

        if args.action == "recuperer":
            sys.stdout.buffer.write(recuperer(args.nom))
            return 0

    except (nextcloud.NonConfigure, nextcloud.NextcloudIndisponible, ValueError) as e:
        print(f"ERREUR : {e}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
