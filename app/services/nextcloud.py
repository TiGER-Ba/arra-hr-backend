"""Client WebDAV Nextcloud — stockage des documents du personnel.

WebDAV n'est que du HTTP : `PROPFIND` (lister), `MKCOL` (créer un dossier),
`PUT`, `GET`, `DELETE`, `MOVE`. `httpx` suffit, aucune dépendance de plus.

⚠️ **LE garde-fou de ce module.** La même instance Nextcloud héberge
`6.9 Recrutement`, qui appartient à l'autre plateforme. Tout chemin manipulé ici
est résolu **sous la racine configurée** (`6.10 RH Admin web` par défaut), et
`_chemin_sur` refuse `..`, les chemins absolus et les segments vides. Cette
application ne doit pas pouvoir écrire une ligne en dehors de son dossier, même
si un nom de fichier est fabriqué pour ça — c'est le pendant de `safe_join()`
pour le disque local.

Le mot de passe est chiffré en base (`services/secrets.py`) et n'est déchiffré
qu'ici, le temps de la requête.
"""
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

# Nextcloud répond parfois lentement sur un gros PUT ; 30 s couvre les 10 Mo
# autorisés sans laisser une requête pendre indéfiniment.
DELAI = httpx.Timeout(30.0, connect=10.0)

RACINE_DEFAUT = "6.10 RH Admin web"

CLE_URL = "nextcloud_url"
CLE_UTILISATEUR = "nextcloud_utilisateur"
CLE_MOT_DE_PASSE = "nextcloud_mot_de_passe"   # chiffré
CLE_RACINE = "nextcloud_racine"

_NS = {"d": "DAV:"}


class NextcloudIndisponible(Exception):
    """Nextcloud n'a pas répondu, ou a refusé. Message destiné à l'utilisateur."""


class CheminInvalide(Exception):
    """Chemin qui sortirait de la racine configurée. Ne doit jamais arriver."""


class NonConfigure(Exception):
    """Aucun serveur Nextcloud renseigné dans le paramétrage."""


@dataclass(frozen=True)
class Config:
    url: str
    utilisateur: str
    mot_de_passe: str
    racine: str

    @property
    def base(self) -> str:
        """Racine WebDAV du compte : .../remote.php/dav/files/<utilisateur>/"""
        return f"{self.url}/remote.php/dav/files/{quote(self.utilisateur)}/"


def config(db: Session, *, obligatoire: bool = True) -> Config | None:
    """Configuration enregistrée, ou None si Nextcloud n'est pas branché.

    `obligatoire=True` lève plutôt que de rendre None : la plupart des appelants
    ne peuvent rien faire sans, autant échouer avec un message net.
    """
    from app.services.parametrage import get_param, get_param_secret

    url = (get_param(db, CLE_URL, "") or "").strip().rstrip("/")
    utilisateur = (get_param(db, CLE_UTILISATEUR, "") or "").strip()
    mot_de_passe = get_param_secret(db, CLE_MOT_DE_PASSE, "")
    racine = (get_param(db, CLE_RACINE, "") or "").strip() or RACINE_DEFAUT

    if not (url and utilisateur and mot_de_passe):
        if obligatoire:
            raise NonConfigure(
                "Nextcloud n'est pas configuré. Renseignez-le dans "
                "Paramétrage → Stockage des documents."
            )
        return None
    return Config(url=url, utilisateur=utilisateur, mot_de_passe=mot_de_passe, racine=racine)


def est_configure(db: Session) -> bool:
    return config(db, obligatoire=False) is not None


# ── Chemins ──────────────────────────────────────────────────────────────────

def _segments_surs(chemin: str) -> list[str]:
    """Segments d'un chemin relatif, ou `CheminInvalide`.

    ⚠️ Refuse tout ce qui pourrait sortir du dossier : remontée `..`, chemin
    absolu, segment vide (un `//` que le serveur pourrait réinterpréter), et les
    séparateurs Windows qui deviendraient des noms de fichiers absurdes.
    """
    if chemin is None:
        raise CheminInvalide("Chemin absent")
    brut = str(chemin).replace("\\", "/").strip()
    if brut.startswith("/"):
        raise CheminInvalide(f"Chemin absolu refusé : {chemin!r}")
    segments = [s for s in brut.split("/") if s not in ("", ".")]
    for s in segments:
        if s == "..":
            raise CheminInvalide(f"Remontée de dossier refusée : {chemin!r}")
        if "\x00" in s:
            raise CheminInvalide("Caractère nul dans le chemin")
    return segments


def _chemin_sur(cfg: Config, chemin: str = "") -> str:
    """Chemin relatif au compte WebDAV, toujours SOUS la racine configurée."""
    racine = _segments_surs(cfg.racine)
    return "/".join(racine + _segments_surs(chemin))


def _url(cfg: Config, chemin: str = "") -> str:
    relatif = _chemin_sur(cfg, chemin)
    return cfg.base + quote(relatif)


def _client(cfg: Config) -> httpx.Client:
    return httpx.Client(auth=(cfg.utilisateur, cfg.mot_de_passe), timeout=DELAI,
                        follow_redirects=False)


def _verifier(reponse: httpx.Response, action: str, attendus: tuple[int, ...]) -> None:
    if reponse.status_code in attendus:
        return
    if reponse.status_code in (401, 403):
        raise NextcloudIndisponible(
            f"Nextcloud refuse l'accès ({reponse.status_code}) : vérifiez "
            f"l'utilisateur, le mot de passe et les droits sur le dossier."
        )
    if reponse.status_code == 404:
        raise NextcloudIndisponible(f"{action} : introuvable sur Nextcloud.")
    raise NextcloudIndisponible(
        f"{action} : Nextcloud a répondu {reponse.status_code}."
    )


# ── Opérations ───────────────────────────────────────────────────────────────

def tester(cfg: Config) -> dict:
    """Vérifie que le serveur répond ET que la racine est accessible en écriture.

    Un simple accès en lecture ne prouverait rien : le dépôt échouerait quand
    même au premier document. On crée donc la racine si elle manque.
    """
    try:
        with _client(cfg) as c:
            reponse = c.request("PROPFIND", cfg.base, headers={"Depth": "0"})
            _verifier(reponse, "Connexion", (207, 200))
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Serveur injoignable : {e}") from e

    creee = assurer_dossier(cfg, "")
    return {
        "ok": True,
        "racine": cfg.racine,
        "racine_creee": creee,
        "message": (f"Connexion réussie. Dossier « {cfg.racine} » "
                    + ("créé." if creee else "trouvé.")),
    }


def assurer_dossier(cfg: Config, chemin: str = "") -> bool:
    """Crée le dossier et ses parents s'ils manquent. Rend True si créé.

    `MKCOL` sur un dossier existant répond 405 : ce n'est pas une erreur, c'est
    la réponse « il est déjà là ». Rejouer l'appel est donc sans effet.
    """
    segments = _segments_surs(cfg.racine) + _segments_surs(chemin)
    cree = False
    try:
        with _client(cfg) as c:
            for i in range(1, len(segments) + 1):
                url = cfg.base + quote("/".join(segments[:i]))
                reponse = c.request("MKCOL", url)
                if reponse.status_code in (201,):
                    cree = True
                elif reponse.status_code in (405, 301, 302):
                    pass  # existe déjà
                else:
                    _verifier(reponse, f"Création de « {segments[i - 1]} »", (201, 405))
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Création de dossier impossible : {e}") from e
    return cree


def lister(cfg: Config, chemin: str = "") -> list[dict]:
    """Contenu direct d'un dossier : [{nom, chemin, dossier, taille, modifie}].

    Un dossier absent rend une liste vide plutôt qu'une erreur : appeler ceci
    sur le dossier d'un salarié qui n'a encore rien reçu est normal.
    """
    prefixe = _chemin_sur(cfg, chemin)
    try:
        with _client(cfg) as c:
            reponse = c.request("PROPFIND", _url(cfg, chemin), headers={"Depth": "1"})
            if reponse.status_code == 404:
                return []
            _verifier(reponse, f"Lecture de « {chemin or cfg.racine} »", (207,))
            racine_xml = ElementTree.fromstring(reponse.content)
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Lecture impossible : {e}") from e
    except ElementTree.ParseError as e:
        raise NextcloudIndisponible(f"Réponse Nextcloud illisible : {e}") from e

    base_chemin = urlparse(cfg.base).path
    racine_str = "/".join(_segments_surs(cfg.racine))
    entrees: list[dict] = []
    for reponse_xml in racine_xml.findall("d:response", _NS):
        href = (reponse_xml.findtext("d:href", "", _NS) or "").strip()
        relatif = unquote(urlparse(href).path)
        if relatif.startswith(base_chemin):
            relatif = relatif[len(base_chemin):]
        relatif = relatif.strip("/")
        if relatif == prefixe or not relatif:
            continue  # le dossier lui-même, pas son contenu

        propstat = reponse_xml.find("d:propstat/d:prop", _NS)
        if propstat is None:
            continue
        dossier = propstat.find("d:resourcetype/d:collection", _NS) is not None
        taille = propstat.findtext("d:getcontentlength", "", _NS)
        # Chemin RELATIF à la racine : c'est la seule forme que l'application
        # manipule, jamais le chemin absolu du serveur — un chemin absolu qui
        # circulerait finirait par être renvoyé à `_chemin_sur`, qui le refuse.
        nu = relatif.rstrip("/")
        chemin_relatif = nu[len(racine_str) + 1:] if nu.startswith(racine_str + "/") else nu
        entrees.append({
            "nom": nu.split("/")[-1],
            "chemin": chemin_relatif,
            "dossier": dossier,
            "taille": int(taille) if (taille or "").isdigit() else None,
            "modifie": propstat.findtext("d:getlastmodified", "", _NS) or None,
            "type_mime": propstat.findtext("d:getcontenttype", "", _NS) or None,
        })
    return entrees


def envoyer(cfg: Config, chemin: str, contenu: bytes, type_mime: str | None = None) -> str:
    """Dépose un fichier. Le dossier parent est créé au besoin. Rend le chemin."""
    segments = _segments_surs(chemin)
    if not segments:
        raise CheminInvalide("Nom de fichier absent")
    parent = "/".join(segments[:-1])
    if parent:
        assurer_dossier(cfg, parent)

    entetes = {"Content-Type": type_mime or "application/octet-stream"}
    try:
        with _client(cfg) as c:
            reponse = c.put(_url(cfg, chemin), content=contenu, headers=entetes)
            _verifier(reponse, f"Dépôt de « {segments[-1]} »", (201, 204))
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Dépôt impossible : {e}") from e
    return "/".join(segments)


def lire(cfg: Config, chemin: str) -> bytes:
    try:
        with _client(cfg) as c:
            reponse = c.get(_url(cfg, chemin))
            _verifier(reponse, f"Téléchargement de « {chemin} »", (200,))
            return reponse.content
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Téléchargement impossible : {e}") from e


def existe(cfg: Config, chemin: str) -> bool:
    try:
        with _client(cfg) as c:
            reponse = c.request("PROPFIND", _url(cfg, chemin), headers={"Depth": "0"})
            return reponse.status_code == 207
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Vérification impossible : {e}") from e


def supprimer(cfg: Config, chemin: str) -> None:
    """Supprime un fichier. Un fichier déjà absent n'est pas une erreur."""
    if not _segments_surs(chemin):
        raise CheminInvalide("Suppression de la racine refusée")
    try:
        with _client(cfg) as c:
            reponse = c.delete(_url(cfg, chemin))
            _verifier(reponse, f"Suppression de « {chemin} »", (204, 200, 404))
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Suppression impossible : {e}") from e


def deplacer(cfg: Config, source: str, destination: str) -> None:
    """Renomme ou déplace, SOUS la racine des deux côtés (`_url` s'en assure)."""
    segments = _segments_surs(destination)
    if not segments:
        raise CheminInvalide("Destination absente")
    parent = "/".join(segments[:-1])
    if parent:
        assurer_dossier(cfg, parent)
    try:
        with _client(cfg) as c:
            reponse = c.request(
                "MOVE", _url(cfg, source),
                headers={"Destination": _url(cfg, destination), "Overwrite": "F"},
            )
            _verifier(reponse, f"Déplacement de « {source} »", (201, 204))
    except httpx.HTTPError as e:
        raise NextcloudIndisponible(f"Déplacement impossible : {e}") from e
