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
import os
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

#: Une sauvegarde complète = DEUX fichiers portant le même horodatage.
#:
#: ⚠️ Le second n'est pas un confort. La signature et le cachet scannés sont
#: des **originaux numérisés** : perdus, ils ne se reconstruisent pas — il
#: faudrait retrouver le tampon physique et le rescanner. Et sans eux, TOUS les
#: documents générés sortent amputés. Aucune restauration de base ne répare ça.
SUFFIXE_BASE = ".sql.gz"
SUFFIXE_FICHIERS = "_fichiers.tar.gz"
PREFIXE = "arra-admin_"

#: ⚠️ Motif STRICT des fichiers que la rotation s'autorise à supprimer.
#: La rotation efface des données sur un drive partagé : elle ne doit jamais
#: pouvoir toucher un fichier qu'elle n'a pas écrit elle-même. Tout ce qui ne
#: correspond pas exactement est ignoré, y compris un dump déposé à la main.
MOTIF_SAUVEGARDE = re.compile(
    r"^arra-admin_(?P<horodatage>\d{8}-\d{6})"
    r"(?P<genre>\.sql\.gz|_fichiers\.tar\.gz)$"
)

#: En dessous, le dump est forcément tronqué ou vide — on refuse de l'envoyer
#: et surtout de faire tourner la rotation derrière.
TAILLE_MINIMALE = 1024


def horodatage(instant: datetime | None = None) -> str:
    """« 20260921-050000 », en **UTC**.

    Le serveur est en CEST : un nom en heure locale reculerait d'une heure au
    passage à l'heure d'hiver, cassant l'ordre de rotation une nuit par an.
    """
    return (instant or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")


def nom_sauvegarde(instant: datetime | None = None, fichiers: bool = False) -> str:
    """Nom d'une des deux pièces d'une sauvegarde, triable alphabétiquement."""
    suffixe = SUFFIXE_FICHIERS if fichiers else SUFFIXE_BASE
    return f"{PREFIXE}{horodatage(instant)}{suffixe}"


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
        "sauvegardes_conservees": len(_par_horodatage(restantes)),
    }


def _sauvegardes(cfg) -> list[str]:
    """Nos sauvegardes présentes, les plus récentes d'abord."""
    return sorted(
        (e["nom"] for e in nextcloud.lister(cfg, "")
         if not e["dossier"] and MOTIF_SAUVEGARDE.match(e["nom"])),
        reverse=True,
    )


def _par_horodatage(noms) -> dict[str, list[str]]:
    """Regroupe les pièces par sauvegarde : {« 20260921-050000 »: [base, fichiers]}."""
    groupes: dict[str, list[str]] = {}
    for nom in noms:
        m = MOTIF_SAUVEGARDE.match(nom)
        if m:
            groupes.setdefault(m.group("horodatage"), []).append(nom)
    return groupes


def _rotation(cfg) -> list[str]:
    """Garde les `RETENTION` sauvegardes les plus récentes, pièces comprises.

    ⚠️ La rétention compte des **sauvegardes**, pas des fichiers. Une
    sauvegarde en compte deux depuis l'ajout des fichiers déposés : garder
    « les 7 derniers fichiers » ne garderait plus que 3 jours et demi.

    ⚠️ Ne supprime QUE ce que `MOTIF_SAUVEGARDE` reconnaît. Un fichier déposé
    à la main dans ce dossier, une archive d'un autre outil, un sous-dossier :
    rien de tout cela n'est touché.
    """
    groupes = _par_horodatage(_sauvegardes(cfg))
    perimes = sorted(groupes, reverse=True)[RETENTION:]
    a_supprimer = [nom for h in perimes for nom in sorted(groupes[h])]
    for nom in a_supprimer:
        nextcloud.supprimer(cfg, nom)
    return a_supprimer


def archiver_fichiers(sortie) -> dict:
    """Écrit un tar.gz du dossier `uploads` sur `sortie` (flux binaire).

    ⚠️ **Pourquoi sauvegarder ce dossier.** Il ne contient pas que des fichiers
    régénérables : `uploads/parametrage/` porte la **signature et le cachet
    scannés**. Ce sont des originaux numérisés — perdus, ils ne se
    reconstruisent pas, il faudrait retrouver le tampon physique et le
    rescanner. Et sans eux, TOUS les documents générés sortent amputés.
    Quelques Mo contre une panne qu'aucune restauration de base ne répare.

    Passe par `tarfile` plutôt que par la commande `tar` : l'image de l'API est
    une `python:slim`, et dépendre d'un binaire qui pourrait disparaître d'une
    version de base à l'autre ferait échouer la sauvegarde en silence.
    """
    import tarfile

    from app.config import settings

    racine = os.path.abspath(settings.UPLOADS_DIR)
    fichiers = 0
    octets_sources = 0

    # `gzip` sur un flux non « seekable » : mode « w|gz », pas « w:gz ».
    with tarfile.open(fileobj=sortie, mode="w|gz") as archive:
        if os.path.isdir(racine):
            for dossier, _, noms in os.walk(racine):
                for nom in sorted(noms):
                    chemin = os.path.join(dossier, nom)
                    interne = os.path.relpath(chemin, racine).replace("\\", "/")
                    try:
                        archive.add(chemin, arcname=f"uploads/{interne}")
                        fichiers += 1
                        octets_sources += os.path.getsize(chemin)
                    except OSError as e:
                        # Un fichier illisible ne doit pas faire échouer toute
                        # la sauvegarde — mais il doit se voir.
                        print(f"  ⚠️  ignoré : {interne} ({e})", file=sys.stderr)
    return {"fichiers": fichiers, "octets_sources": octets_sources, "racine": racine}


def extraire_fichiers(entree) -> dict:
    """Réinjecte un tar.gz d'`uploads` lu sur `entree`. Fusionne, n'efface pas.

    ⚠️ **Fusion et non remplacement.** On ne vide pas `uploads/` avant
    d'extraire : un fichier déposé depuis la sauvegarde serait perdu sans
    recours. Un fichier présent des deux côtés est écrasé par celui de
    l'archive — c'est le but quand on restaure une signature disparue.

    ⚠️ **Filtre anti-évasion.** L'archive vient du réseau. Sans contrôle, une
    entrée nommée `../../etc/...` ou un lien symbolique écrirait hors du
    dossier. `filter="data"` (Python 3.12) refuse les chemins absolus, les
    remontées et les liens ; on vérifie en plus le préfixe `uploads/`.
    """
    import tarfile

    from app.config import settings

    racine = os.path.abspath(settings.UPLOADS_DIR)
    parent = os.path.dirname(racine)
    extraits = ignores = 0

    def _acceptable(nom: str) -> bool:
        """Nos archives préfixent tout par « uploads/ ». Le reste est suspect."""
        segments = nom.replace("\\", "/").split("/")
        return (nom.startswith("uploads/")
                and ".." not in segments
                and not nom.startswith("/")
                and "\x00" not in nom)

    with tarfile.open(fileobj=entree, mode="r|gz") as archive:
        for membre in archive:
            # ⚠️ Écarter AVANT d'appeler `extract`. Un filtre qui rend None
            # fait sauter l'entrée en silence : `extract` réussit, rien n'est
            # écrit, et on compterait un fichier « restauré » qui ne l'est pas.
            # Une archive piégée passerait alors sans le moindre avertissement.
            if not _acceptable(membre.name):
                ignores += 1
                print(f"  ⚠️  ÉCARTÉ (chemin hors périmètre) : {membre.name}",
                      file=sys.stderr)
                continue
            try:
                # `data_filter` refuse en plus les liens, les périphériques et
                # les permissions exotiques (Python 3.12).
                archive.extract(membre, path=parent, filter="data")
            except Exception as e:  # noqa: BLE001
                ignores += 1
                print(f"  ⚠️  ignoré : {membre.name} ({e})", file=sys.stderr)
                continue
            extraits += 1

    return {"extraits": extraits, "ignores": ignores, "racine": racine}


def lister() -> dict:
    """Sauvegardes présentes — sert à vérifier que la rotation vit."""
    db = SessionLocal()
    try:
        cfg = config_sauvegardes(db)
        noms = _sauvegardes(cfg)
        details = {e["nom"]: e for e in nextcloud.lister(cfg, "")}
    finally:
        db.close()
    groupes = _par_horodatage(noms)
    return {
        "racine": cfg.racine,
        "nombre": len(groupes),
        "pieces": len(noms),
        # ⚠️ Une sauvegarde incomplète (base sans fichiers, ou l'inverse) doit
        # se voir : c'est le symptôme d'une exécution interrompue.
        "incompletes": sorted(h for h, p in groupes.items() if len(p) < 2),
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

    p_nom = sous.add_parser("nom", help="affiche le nom à utiliser pour maintenant")
    p_nom.add_argument("--fichiers", action="store_true",
                       help="nom de l'archive des fichiers plutôt que du dump")

    sous.add_parser("archiver-fichiers",
                    help="écrit un tar.gz du dossier uploads sur stdout")
    sous.add_parser("extraire-fichiers",
                    help="lit un tar.gz sur stdin et le fusionne dans uploads")

    p_rec = sous.add_parser("recuperer", help="écrit une sauvegarde sur stdout")
    p_rec.add_argument("--nom", required=True)

    args = parseur.parse_args(argv)

    try:
        if args.action == "nom":
            print(nom_sauvegarde(fichiers=args.fichiers))
            return 0

        if args.action == "archiver-fichiers":
            bilan = archiver_fichiers(sys.stdout.buffer)
            print(f"  {bilan['fichiers']} fichier(s) depuis {bilan['racine']}",
                  file=sys.stderr)
            return 0

        if args.action == "extraire-fichiers":
            bilan = extraire_fichiers(sys.stdin.buffer)
            print(f"  {bilan['extraits']} fichier(s) restauré(s) dans {bilan['racine']}")
            if bilan["ignores"]:
                print(f"  ⚠️  {bilan['ignores']} entrée(s) écartée(s)")
            return 0

        if args.action == "deposer":
            compte_rendu = deposer(sys.stdin.buffer.read(), args.nom)
            mo = compte_rendu["octets"] / 1048576
            print(f"  déposé   : {compte_rendu['depose']} ({mo:.1f} Mo)")
            print(f"  dossier  : {compte_rendu['racine']}")
            print(f"  sauvegardes conservées : {compte_rendu['sauvegardes_conservees']}"
                  f" ({len(compte_rendu['conservees'])} pièces)")
            for nom in compte_rendu["supprimees"]:
                print(f"  purgée   : {nom}")
            return 0

        if args.action == "lister":
            etat = lister()
            print(f"  dossier     : {etat['racine']}")
            print(f"  sauvegardes : {etat['nombre']} ({etat['pieces']} pièces)")
            if etat["incompletes"]:
                print("  ⚠️  incomplètes (une seule pièce sur deux) : "
                      + ", ".join(etat["incompletes"]))
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
