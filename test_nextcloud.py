"""Stockage Nextcloud : confinement des chemins, secrets, nommage des dossiers.

Le test central est le **confinement** : la même instance Nextcloud héberge
`6.9 Recrutement`, qui appartient à l'autre plateforme. Aucun nom de fichier,
aussi tordu soit-il, ne doit permettre à cette application d'écrire en dehors de
sa racine. C'est le pendant de `test_securite.py` pour le disque local.
"""
import os
import sys

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "cle-de-test-suffisamment-longue-pour-32+")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def refuse(fn, libelle):
    from app.services.nextcloud import CheminInvalide
    try:
        fn()
        verifier(False, libelle, "aucune erreur levée")
    except CheminInvalide:
        verifier(True, libelle)
    except Exception as e:  # noqa: BLE001
        verifier(False, libelle, f"mauvaise erreur : {type(e).__name__}")


class _Utilisateur:
    def __init__(self, nom, prenom):
        self.nom, self.prenom = nom, prenom


class _Fiche:
    def __init__(self, matricule, nom, prenom, statut):
        self.matricule, self.statut, self.id = matricule, statut, id(self)
        self.utilisateur = _Utilisateur(nom, prenom)


def _fiche(matricule, nom, prenom, statut):
    return _Fiche(matricule, nom, prenom, statut)


class _FauxNextcloud:
    """Nextcloud en mémoire : vérifie les APPELS, sans serveur.

    Compte les listages de la racine — c'est ce qui distingue un rattrapage
    efficace d'un qui relit tout le drive à chaque salarié.
    """

    def __init__(self):
        self.dossiers: list[str] = []
        self.listages_racine = 0
        self.cfg = None

    def _lister(self, cfg, chemin=""):
        if chemin == "":
            self.listages_racine += 1
            return [{"nom": d, "dossier": True} for d in self.dossiers if "/" not in d]
        prefixe = chemin + "/"
        return [{"nom": d[len(prefixe):], "dossier": True}
                for d in self.dossiers if d.startswith(prefixe)]

    def _assurer(self, cfg, chemin=""):
        # Comme le vrai MKCOL récursif : chaque niveau intermédiaire est créé.
        # Sans cela le parent n'existerait pas à la racine, et le rattrapage
        # croirait devoir tout recréer au passage suivant.
        segments = [s for s in chemin.split("/") if s]
        cree = False
        for i in range(1, len(segments) + 1):
            niveau = "/".join(segments[:i])
            if niveau not in self.dossiers:
                self.dossiers.append(niveau)
                cree = True
        return cree

    def _deplacer(self, cfg, source, destination):
        self.dossiers = [
            destination + d[len(source):] if d == source or d.startswith(source + "/") else d
            for d in self.dossiers
        ]

    def executer(self, fonction, fiches):
        """Lance `fonction(db, cfg)` avec ce faux serveur en place."""
        from app.services import dossier_employe as de
        from app.services import nextcloud as nc

        class _Db:
            def query(self, _modele):
                return self

            def order_by(self, _col):
                return self

            def all(self):
                return fiches

        cfg = nc.Config(url="https://x", utilisateur="u", mot_de_passe="p",
                        racine="6.10 RH Admin web")
        origines = (nc.lister, nc.assurer_dossier, nc.deplacer)
        nc.lister, nc.assurer_dossier, nc.deplacer = (
            self._lister, self._assurer, self._deplacer)
        de.nextcloud = nc
        try:
            self.listages_racine = 0
            return fonction(_Db(), cfg)
        finally:
            nc.lister, nc.assurer_dossier, nc.deplacer = origines


def main():
    from app.services import dossier_employe as de
    from app.services import nextcloud as nc
    from app.services import secrets as sec
    from app.services import stockage

    cfg = nc.Config(url="https://exemple.tld", utilisateur="svc",
                    mot_de_passe="x", racine="6.10 RH Admin web")

    print("\n— 1. Aucun chemin ne sort de la racine —")
    # ⚠️ Le scénario redouté : atteindre le dossier de l'AUTRE plateforme.
    for mauvais in ("../6.9 Recrutement/secret.pdf",
                    "a/../../6.9 Recrutement/x.pdf",
                    "..",
                    "../../../../etc/passwd",
                    "/etc/passwd",
                    "\\..\\6.9 Recrutement\\x.pdf"):
        refuse(lambda m=mauvais: nc._chemin_sur(cfg, m), f"refusé : {mauvais!r}")

    print("\n— 2. Un chemin normal reste sous la racine —")
    resolu = nc._chemin_sur(cfg, "ARRA-I024 - Karim/Partagé/bulletin.pdf")
    verifier(resolu == "6.10 RH Admin web/ARRA-I024 - Karim/Partagé/bulletin.pdf",
             "la racine est toujours préfixée", resolu)
    verifier(nc._chemin_sur(cfg, "") == "6.10 RH Admin web",
             "chemin vide = la racine elle-même")
    verifier(nc._chemin_sur(cfg, "a//b/./c") == "6.10 RH Admin web/a/b/c",
             "segments vides et « . » normalisés", nc._chemin_sur(cfg, "a//b/./c"))

    print("\n— 3. L'URL construite vise bien le bon compte —")
    verifier(cfg.base.endswith("/remote.php/dav/files/svc/"), "base WebDAV correcte", cfg.base)
    verifier("6.10%20RH%20Admin%20web" in nc._url(cfg, "x.pdf"),
             "les espaces de la racine sont encodés", nc._url(cfg, "x.pdf"))

    print("\n— 4. Le sous-dossier PORTE la visibilité —")
    verifier(de.sous_dossier(True) == de.PARTAGE, "visible → Partagé")
    verifier(de.sous_dossier(False) == de.ADMINISTRATIF, "non visible → Administratif")
    verifier(de.visibilite_depuis_chemin("ARRA-I024 - K/Partagé/b.pdf") is True,
             "lecture inverse : Partagé → visible")
    verifier(de.visibilite_depuis_chemin("ARRA-I024 - K/Administratif/d.pdf") is False,
             "lecture inverse : Administratif → non visible")
    # Un fichier posé à la racine du dossier n'est rangé nulle part : l'appelant
    # doit pouvoir le distinguer, pour le cacher par prudence.
    verifier(de.visibilite_depuis_chemin("ARRA-I024 - K/perdu.pdf") is None,
             "hors sous-dossier → indéterminé, pas « visible »")

    print("\n— 5. Nommage du dossier : le matricule d'abord —")
    class _U:
        nom, prenom = "BENJELLOUN", "Karim"

    class _E:
        matricule, id, utilisateur = "ARRA-I024", 1, _U()

    verifier(de.nom_dossier(_E()) == "ARRA-I024 - BENJELLOUN Karim",
             "« ARRA-I024 - BENJELLOUN Karim »", de.nom_dossier(_E()))

    class _Sale:
        matricule, id = "ARRA-I0/25", 2
        class utilisateur:  # noqa: N801
            nom, prenom = "DU/PONT", "Jean\\Luc"

    sale = de.nom_dossier(_Sale())
    verifier("/" not in sale and "\\" not in sale,
             "les séparateurs d'un nom sont retirés", sale)

    print("\n— 6. Les deux stockages cohabitent —")
    distant = stockage.chemin_distant("ARRA-I024 - K/Partagé/b.pdf")
    verifier(stockage.est_distant(distant), "un chemin Nextcloud est reconnu")
    verifier(stockage.sans_prefixe(distant) == "ARRA-I024 - K/Partagé/b.pdf",
             "le préfixe se retire proprement")
    # ⚠️ Le cas qui casserait la production : les dépôts déjà en base.
    ancien = "/home/user/app/uploads/depot/employe_ARRA-I024/contrat.pdf"
    verifier(not stockage.est_distant(ancien),
             "un chemin disque historique reste local")
    verifier(stockage.sans_prefixe(ancien) == ancien,
             "et n'est pas modifié au passage")

    print("\n— 7. Le mot de passe n'est jamais lisible en base —")
    clair = "mot-de-passe-nextcloud"
    chiffre = sec.chiffrer(clair)
    verifier(chiffre.startswith(sec.PREFIXE), "valeur marquée « enc:v1: »")
    verifier(clair not in chiffre, "le secret n'apparaît pas dans la valeur stockée")
    verifier(sec.dechiffrer(chiffre) == clair, "aller-retour fidèle")
    verifier(sec.chiffrer(chiffre) == chiffre, "chiffrer deux fois n'empile pas les couches")
    verifier(sec.chiffrer("") == "", "une valeur vide reste vide")

    print("\n— 8. Les secrets écrits AVANT le chiffrement restent lisibles —")
    # Sans cela, la mise à jour couperait l'envoi des emails du jour au lendemain.
    verifier(sec.dechiffrer("ancien-mot-de-passe-en-clair") == "ancien-mot-de-passe-en-clair",
             "valeur sans préfixe rendue telle quelle")

    print("\n— 9. SECRET_KEY changée : message clair, pas une trace d'erreur —")
    from app.config import settings
    memoire = settings.SECRET_KEY
    try:
        settings.SECRET_KEY = "une-tout-autre-cle-de-32-caracteres-mini"
        try:
            sec.dechiffrer(chiffre)
            verifier(False, "déchiffrement refusé après rotation de la clé")
        except sec.SecretIllisible as e:
            verifier("ressaisissez" in str(e).lower(),
                     "l'erreur dit quoi faire", str(e))
    finally:
        settings.SECRET_KEY = memoire

    print("\n— 10. Un secret ne transite pas par `set_param` en clair —")
    import inspect
    from app.services import parametrage
    source = inspect.getsource(parametrage.set_param_secret)
    verifier("chiffrer" in source, "set_param_secret chiffre systématiquement")

    print("\n— 11. Qui mérite un dossier —")
    # Ouvrir un dossier à un brouillon ou à quelqu'un qui n'est jamais venu
    # remplirait le drive de coquilles vides que personne ne nettoierait.
    verifier(not de.merite_dossier("brouillon"), "brouillon → pas de dossier")
    verifier(not de.merite_dossier("desistement"), "désistement → pas de dossier")
    verifier(de.merite_dossier("actif_interne"), "actif interne → dossier")
    verifier(de.merite_dossier("actif_production"), "actif production → dossier")
    # Une personne partie garde ses documents : son dossier doit rester.
    verifier(de.merite_dossier("quitte"), "quitté → dossier conservé")

    print("\n— 12. Rattrapage des comptes existants —")
    faux = _FauxNextcloud()
    fiches = [
        _fiche("ARRA-I001", "BENALI", "Sara", "actif_interne"),
        _fiche("ARRA-E002", "CHAKIR", "Omar", "actif_production"),
        _fiche("ARRA-I003", "TAZI", "Nadia", "brouillon"),
        _fiche("ARRA-I004", "FASSI", "Youssef", "quitte"),
    ]
    rapport = faux.executer(de.creer_dossiers_manquants, fiches)
    verifier(len(rapport["crees"]) == 3, "3 dossiers créés (le brouillon est ignoré)",
             str(rapport["crees"]))
    verifier(rapport["ignores"] == ["ARRA-I003"], "le brouillon est listé comme ignoré",
             str(rapport["ignores"]))
    verifier(all(f"{n}/Partagé" in faux.dossiers and f"{n}/Administratif" in faux.dossiers
                 for n in rapport["crees"]),
             "chaque dossier reçoit ses deux sous-dossiers")

    print("\n— 13. Relancer le rattrapage ne recrée rien —")
    avant = len(faux.dossiers)
    rapport2 = faux.executer(de.creer_dossiers_manquants, fiches)
    verifier(rapport2["crees"] == [], "aucun dossier créé au second passage",
             str(rapport2["crees"]))
    verifier(rapport2["existants"] == 3, "les 3 sont reconnus comme présents",
             str(rapport2["existants"]))
    verifier(len(faux.dossiers) == avant, "aucun dossier en double sur le drive")

    print("\n— 14. La racine n'est listée qu'UNE fois pour tout l'effectif —")
    # Sans cela, chaque salarié relancerait un PROPFIND complet du drive.
    verifier(faux.listages_racine == 1,
             "un seul listage de la racine, quel que soit l'effectif",
             f"{faux.listages_racine} listage(s)")

    print("\n— 15. Un nom qui change RENOMME au lieu de dupliquer —")
    # Sinon les documents de la personne se répartiraient sur deux dossiers.
    fiches[0].utilisateur.nom = "BENALI-IDRISSI"
    faux.executer(de.creer_dossiers_manquants, fiches)
    restants = [d for d in faux.dossiers if d.startswith("ARRA-I001 - ") and "/" not in d]
    verifier(restants == ["ARRA-I001 - BENALI-IDRISSI Sara"],
             "un seul dossier, au nouveau nom", str(restants))

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : STOCKAGE NEXTCLOUD OK")


if __name__ == "__main__":
    main()
