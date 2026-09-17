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


def dossier_existant(cfg: nextcloud.Config, employe: Employe) -> str | None:
    """Dossier du salarié déjà présent, retrouvé par **préfixe de matricule**.

    ⚠️ On ne cherche pas le nom complet : si la personne se marie et change de
    nom, le dossier porte encore l'ancien. Le matricule, lui, ne bouge jamais —
    chercher par préfixe évite de créer un second dossier et d'éparpiller les
    documents de quelqu'un sur deux emplacements.
    """
    prefixe = _prefixe(employe)
    exact = nom_dossier(employe)
    candidats = [
        e["nom"] for e in nextcloud.lister(cfg, "")
        if e["dossier"] and e["nom"].startswith(prefixe)
    ]
    if not candidats:
        return None
    # Le nom exact d'abord : s'il existe, rien à renommer.
    return exact if exact in candidats else sorted(candidats)[0]


def assurer_dossier(cfg: nextcloud.Config, employe: Employe) -> str:
    """Dossier du salarié, créé au besoin, avec ses deux sous-dossiers.

    Si le nom a changé (mariage, correction d'orthographe), le dossier est
    **renommé** plutôt que dupliqué. Le renommage est au mieux : s'il échoue,
    on continue avec l'ancien nom — un document déposé dans un dossier au nom
    périmé vaut mieux qu'un dépôt refusé.
    """
    attendu = nom_dossier(employe)
    actuel = dossier_existant(cfg, employe)

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
