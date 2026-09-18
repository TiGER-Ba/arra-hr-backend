"""Dossier Nextcloud d'un salarié — nommage, résolution, visibilité.

```
6.10 RH Admin web/
└── ARRA-I024 - BENJELLOUN Karim/
    ├── Partagé/          ← le salarié y a accès (bulletins, attestations)
    └── Administratif/    ← RH et admin seulement
```

⚠️ **Le sous-dossier PORTE la règle de visibilité.** C'est le point de ce
découpage : un RH (ou le comptable) qui dépose un fichier directement dans
Nextcloud, sans passer par l'application, obtient la bonne confidentialité du
seul fait de l'endroit où il l'a posé. Une colonne en base ne pourrait pas le
suivre là-bas.

⚠️ Nextcloud **ne décide de rien**. Il ne connaît qu'un compte de service, qui
voit tout. La séparation vaut pour les yeux humains qui parcourent le drive et
pour la synchronisation ; le contrôle d'accès reste celui de l'application,
qui sert les fichiers par ses propres routes JWT. **Ne jamais donner un lien de
partage Nextcloud à un salarié** : il contournerait tout.
"""
import re
import unicodedata

from app.models.employee import Employe
from app.services import nextcloud

#: Sous-dossier des documents que le salarié peut consulter.
PARTAGE = "Partagé"
#: Sous-dossier des documents réservés au RH et à l'admin.
ADMINISTRATIF = "Administratif"

SOUS_DOSSIERS = (PARTAGE, ADMINISTRATIF)

#: Dossiers des comptes supprimés. Le tiret bas le fait remonter en tête de la
#: liste Nextcloud, nettement séparé des dossiers des personnes en poste.
ARCHIVES = "_Archives"

# Nextcloud accepte beaucoup, mais ces caractères cassent les chemins WebDAV ou
# la synchronisation de bureau sous Windows.
_INTERDITS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

SEPARATEUR = " - "


def sous_dossier(visible_employe: bool) -> str:
    """Sous-dossier correspondant à la visibilité. Règle en un seul endroit."""
    return PARTAGE if visible_employe else ADMINISTRATIF


def visibilite_depuis_chemin(chemin: str) -> bool | None:
    """Visibilité déduite du chemin, ou None s'il ne désigne aucun sous-dossier.

    C'est la lecture inverse : elle sert à la synchronisation descendante, pour
    classer un fichier déposé à la main. `None` signifie « rangé nulle part » —
    l'appelant décide alors, et le bon réflexe est de traiter comme
    **non visible** : mieux vaut cacher un document au salarié par excès de
    prudence que lui exposer un dossier disciplinaire.
    """
    segments = [s for s in (chemin or "").replace("\\", "/").split("/") if s]
    for s in segments:
        if s == PARTAGE:
            return True
        if s == ADMINISTRATIF:
            return False
    return None


def _nettoyer(valeur: str) -> str:
    """Nom utilisable comme dossier : sans caractère interdit ni espace double."""
    texte = unicodedata.normalize("NFC", (valeur or "").strip())
    texte = _INTERDITS.sub("", texte)
    return re.sub(r"\s+", " ", texte).strip(" .")


def nom_dossier(employe: Employe) -> str:
    """« ARRA-I024 - BENJELLOUN Karim ».

    Le matricule d'abord : il est **figé à la création** (règle documentée), donc
    c'est la seule partie sur laquelle on peut compter dans la durée. Le nom
    suit, pour que le dossier reste lisible par un humain dans Nextcloud.
    """
    matricule = _nettoyer(employe.matricule) or f"employe-{employe.id}"
    utilisateur = getattr(employe, "utilisateur", None)
    parties = [getattr(utilisateur, "nom", "") or "", getattr(utilisateur, "prenom", "") or ""]
    libelle = _nettoyer(" ".join(p for p in parties if p))
    return f"{matricule}{SEPARATEUR}{libelle}" if libelle else matricule


def _prefixe(employe: Employe) -> str:
    return f"{_nettoyer(employe.matricule) or f'employe-{employe.id}'}{SEPARATEUR}"


def dossiers_racine(cfg: nextcloud.Config) -> list[str]:
    """Noms des dossiers présents à la racine, lus **une seule fois**.

    À passer à `dossier_existant` quand on traite plusieurs salariés d'affilée :
    sans cela, chacun relancerait un PROPFIND de toute la racine — sur trente
    fiches, trente listages complets du drive.
    """
    return [e["nom"] for e in nextcloud.lister(cfg, "") if e["dossier"]]


def dossier_existant(cfg: nextcloud.Config, employe: Employe,
                     connus: list[str] | None = None) -> str | None:
    """Dossier du salarié déjà présent, retrouvé par **préfixe de matricule**.

    ⚠️ On ne cherche pas le nom complet : si la personne se marie et change de
    nom, le dossier porte encore l'ancien. Le matricule, lui, ne bouge jamais —
    chercher par préfixe évite de créer un second dossier et d'éparpiller les
    documents de quelqu'un sur deux emplacements.
    """
    prefixe = _prefixe(employe)
    exact = nom_dossier(employe)
    noms = connus if connus is not None else dossiers_racine(cfg)
    candidats = [n for n in noms if n.startswith(prefixe)]
    if not candidats:
        return None
    # Le nom exact d'abord : s'il existe, rien à renommer.
    return exact if exact in candidats else sorted(candidats)[0]


def merite_dossier(statut: str | None) -> bool:
    """Un dossier n'est créé que pour une fiche qui recevra des documents.

    Un **brouillon** est une saisie en cours et un **désistement** n'est jamais
    venu : leur ouvrir un dossier remplirait le drive de coquilles vides que
    personne ne nettoierait. Une fiche **quittée** en garde un — ses documents
    existent et doivent rester accessibles.
    """
    from app.services import statuts as S

    return statut not in (S.BROUILLON, S.DESISTEMENT)


def assurer_dossier(cfg: nextcloud.Config, employe: Employe,
                    connus: list[str] | None = None) -> str:
    """Dossier du salarié, créé au besoin, avec ses deux sous-dossiers.

    Si le nom a changé (mariage, correction d'orthographe), le dossier est
    **renommé** plutôt que dupliqué. Le renommage est au mieux : s'il échoue,
    on continue avec l'ancien nom — un document déposé dans un dossier au nom
    périmé vaut mieux qu'un dépôt refusé.
    """
    attendu = nom_dossier(employe)
    actuel = dossier_existant(cfg, employe, connus)

    if actuel and actuel != attendu:
        try:
            nextcloud.deplacer(cfg, actuel, attendu)
            actuel = attendu
        except nextcloud.NextcloudIndisponible:
            pass  # on garde l'ancien nom, les documents restent joignables

    dossier = actuel or attendu
    for sd in SOUS_DOSSIERS:
        nextcloud.assurer_dossier(cfg, f"{dossier}/{sd}")
    return dossier


def chemin_document(dossier: str, visible_employe: bool, nom_fichier: str) -> str:
    """Chemin relatif d'un document dans le dossier du salarié."""
    nom = _nettoyer(nom_fichier) or "document"
    return f"{dossier}/{sous_dossier(visible_employe)}/{nom}"


def nom_disponible(cfg: nextcloud.Config, dossier: str, visible_employe: bool,
                   nom_fichier: str) -> str:
    """Nom libre dans le sous-dossier : « contrat.pdf » → « contrat_1.pdf ».

    Deux bulletins du même mois déposés deux fois ne doivent pas s'écraser
    silencieusement — le second serait perdu sans que personne le sache.
    """
    base = _nettoyer(nom_fichier) or "document"
    tige, point, extension = base.rpartition(".")
    if not point:
        tige, extension = base, ""
    suffixe = f".{extension}" if extension else ""

    existants = {e["nom"] for e in nextcloud.lister(cfg, f"{dossier}/{sous_dossier(visible_employe)}")}
    candidat = base
    compteur = 1
    while candidat in existants:
        candidat = f"{tige}_{compteur}{suffixe}"
        compteur += 1
    return candidat


# ── Création automatique ─────────────────────────────────────────────────────

def preparer(db, employe: Employe) -> str | None:
    """Crée (ou renomme) le dossier du salarié. **Ne lève jamais.**

    Appelée depuis la création et la modification d'un compte. Le dossier est un
    confort : le comptable et le RH trouvent une place prête au lieu de devoir
    la fabriquer à la main avec le nom exact. Ce n'est pas une raison de refuser
    une embauche parce qu'un serveur de fichiers ne répond pas — d'où le
    silence en cas d'échec. Le dossier sera créé au premier dépôt, comme avant.

    Rend le nom du dossier, ou None si rien n'a été fait.
    """
    import logging

    if employe is None or not merite_dossier(getattr(employe, "statut", None)):
        return None
    try:
        cfg = nextcloud.config(db, obligatoire=False)
        if cfg is None:
            return None
        return assurer_dossier(cfg, employe)
    except Exception as e:  # noqa: BLE001 — jamais bloquant, par conception
        logging.getLogger(__name__).warning(
            "Dossier Nextcloud non créé pour %s : %s",
            getattr(employe, "matricule", "?"), e,
        )
        return None


def creer_dossiers_manquants(db, cfg: nextcloud.Config) -> dict:
    """Ouvre le dossier des salariés qui n'en ont pas encore.

    Sert au rattrapage : les comptes créés **avant** le branchement de Nextcloud
    n'ont pas de dossier, et le comptable qui veut y déposer un bulletin n'a
    nulle part où le mettre.

    ⚠️ La racine n'est listée **qu'une fois** : la relire par salarié
    multiplierait les allers-retours par l'effectif. Idempotent — un dossier
    déjà là n'est pas recréé, et relancer l'opération ne coûte qu'un listage.
    """
    from app.models.employee import Employe as _Employe

    connus = dossiers_racine(cfg)
    rapport = {"crees": [], "existants": 0, "ignores": [], "echecs": []}

    employes = db.query(_Employe).order_by(_Employe.matricule).all()
    for emp in employes:
        if not merite_dossier(emp.statut):
            # Brouillon ou désistement : pas de coquille vide dans le drive.
            rapport["ignores"].append(emp.matricule)
            continue
        deja = dossier_existant(cfg, emp, connus)
        try:
            nom = assurer_dossier(cfg, emp, connus)
        except nextcloud.NextcloudIndisponible as e:
            rapport["echecs"].append({"matricule": emp.matricule, "raison": str(e)})
            continue
        if deja:
            rapport["existants"] += 1
        else:
            rapport["crees"].append(nom)
            connus.append(nom)   # évite de le relister au salarié suivant

    rapport["total"] = len(employes)
    return rapport


def archiver(db, employe: Employe) -> str | None:
    """Déplace le dossier d'un compte supprimé dans `_Archives/`. **Ne lève jamais.**

    ⚠️ On n'efface PAS les fichiers. Un contrat de travail et des bulletins de
    paie doivent être conservés des années : les détruire d'un clic dans une
    liste supprimerait des pièces que l'employeur est tenu de garder, et la
    suppression d'un compte est trop facile pour emporter ça avec elle.

    Mais les laisser à la racine serait pire à l'usage : au bout de deux ans le
    drive contient des dossiers de gens partis, indiscernables des actifs, et
    l'application ne les connaît plus — ses lignes ont été supprimées avec le
    compte. L'archive sépare les deux sans rien perdre.

    Rend le chemin d'archive, ou None si rien n'a été fait.
    """
    import logging

    if employe is None:
        return None
    try:
        cfg = nextcloud.config(db, obligatoire=False)
        if cfg is None:
            return None
        actuel = dossier_existant(cfg, employe)
        if not actuel:
            return None

        nextcloud.assurer_dossier(cfg, ARCHIVES)
        # Deux personnes ne partagent pas de matricule, mais un dossier
        # réarchivé (compte recréé puis resupprimé) ne doit pas écraser le
        # premier : le MOVE refuserait, et les documents seraient perdus.
        destination = f"{ARCHIVES}/{actuel}"
        compteur = 1
        while nextcloud.existe(cfg, destination):
            destination = f"{ARCHIVES}/{actuel} ({compteur})"
            compteur += 1

        nextcloud.deplacer(cfg, actuel, destination)
        return destination
    except Exception as e:  # noqa: BLE001 — jamais bloquant, par conception
        logging.getLogger(__name__).warning(
            "Dossier Nextcloud non archivé pour %s : %s",
            getattr(employe, "matricule", "?"), e,
        )
        return None
