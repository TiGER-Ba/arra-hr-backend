"""Où vivent les fichiers : Nextcloud ou le disque local.

`DepotDocument.chemin_fichier` porte désormais **deux formes** :

| Forme | Exemple | Lecture |
|---|---|---|
| Nextcloud | `nextcloud:ARRA-I024 - Karim/Partagé/bulletin.pdf` | WebDAV |
| Disque (historique) | `/home/user/app/uploads/depot/employe_ARRA-I024/x.pdf` | `open()` |

⚠️ **Les lignes existantes ne sont pas touchées.** Sans préfixe, on lit le
disque comme avant. C'est ce qui rend la bascule réversible : brancher Nextcloud
n'invalide aucun document déjà déposé, et si la connexion tombe, l'historique
reste consultable. La migration des anciens fichiers est un geste **explicite**
(`migrer_vers_nextcloud.py`), jamais un effet de bord du démarrage.

⚠️ Nextcloud n'est pas l'autorité d'accès : ce module rend des **octets**, et
c'est la route appelante qui a déjà vérifié le droit d'y accéder (rôle,
appartenance de la fiche, `visible_employe`).
"""
import os

from sqlalchemy.orm import Session

from app.services import nextcloud

PREFIXE = "nextcloud:"


class FichierIntrouvable(Exception):
    """Le fichier n'est plus là où la base dit qu'il est."""


def est_distant(chemin: str | None) -> bool:
    return bool(chemin) and chemin.startswith(PREFIXE)


def chemin_distant(chemin_relatif: str) -> str:
    """Valeur à écrire en base pour un fichier déposé sur Nextcloud."""
    return f"{PREFIXE}{chemin_relatif}"


def sans_prefixe(chemin: str) -> str:
    return chemin[len(PREFIXE):] if est_distant(chemin) else chemin


def actif(db: Session) -> bool:
    """Vrai si les NOUVEAUX dépôts doivent partir sur Nextcloud."""
    return nextcloud.est_configure(db)


def lire(db: Session, chemin: str) -> bytes:
    """Contenu du fichier, quel que soit son emplacement.

    Les erreurs sont distinguées volontairement : « le fichier n'existe pas »
    (404 côté appelant) n'est pas « Nextcloud ne répond pas » (503). Les
    confondre enverrait le RH chercher un document qui est en fait bien là.
    """
    if est_distant(chemin):
        cfg = nextcloud.config(db)          # lève NonConfigure si débranché
        return nextcloud.lire(cfg, sans_prefixe(chemin))

    if not chemin or not os.path.exists(chemin):
        raise FichierIntrouvable("Fichier introuvable sur le serveur")
    with open(chemin, "rb") as f:
        return f.read()


def supprimer(db: Session, chemin: str) -> None:
    """Retire le fichier. Un fichier déjà absent n'est pas une erreur.

    ⚠️ Supprimer la ligne en base sans supprimer le fichier laisserait un
    document orphelin dans le dossier du salarié, que la synchronisation
    descendante réimporterait aussitôt — la suppression semblerait ne pas
    fonctionner.
    """
    if est_distant(chemin):
        cfg = nextcloud.config(db, obligatoire=False)
        if cfg:
            nextcloud.supprimer(cfg, sans_prefixe(chemin))
        return
    if chemin and os.path.exists(chemin):
        os.remove(chemin)


def deposer(db: Session, employe, contenu: bytes, nom_fichier: str,
            type_mime: str | None, visible_employe: bool) -> str:
    """Écrit un fichier dans le dossier du salarié et rend son `chemin_fichier`.

    Seul endroit qui décide **où** écrire : Nextcloud s'il est branché, le
    disque sinon. Appelé depuis le dépôt manuel du RH et depuis la clôture
    d'une demande de bulletin — deux copies de cet aiguillage auraient divergé.

    ⚠️ Lève `NextcloudIndisponible` plutôt que de se rabattre sur le disque : un
    document qui atterrit silencieusement ailleurs que dans le drive serait
    invisible au comptable et au RH qui le cherchent dans Nextcloud.
    """
    import os

    from app.config import settings
    from app.services import dossier_employe
    from app.services.security import safe_filename, safe_join

    nom_sur = safe_filename(nom_fichier)

    cfg = nextcloud.config(db, obligatoire=False)
    if cfg:
        # ⚠️ Le SOUS-DOSSIER porte la visibilité : un document non visible part
        # dans « Administratif », que le salarié ne voit pas.
        dossier = dossier_employe.assurer_dossier(cfg, employe)
        libre = dossier_employe.nom_disponible(cfg, dossier, visible_employe, nom_sur)
        relatif = nextcloud.envoyer(
            cfg,
            dossier_employe.chemin_document(dossier, visible_employe, libre),
            contenu,
            type_mime,
        )
        return chemin_distant(relatif)

    depot_dir = os.path.join(settings.UPLOADS_DIR, "depot")
    dossier = safe_join(depot_dir, f"employe_{safe_filename(employe.matricule, 'inconnu')}")
    os.makedirs(dossier, exist_ok=True)
    tige, extension = os.path.splitext(nom_sur)
    cible = safe_join(dossier, nom_sur)
    compteur = 1
    while os.path.exists(cible):
        cible = safe_join(dossier, f"{tige}_{compteur}{extension}")
        compteur += 1
    with open(cible, "wb") as f:
        f.write(contenu)
    return cible
