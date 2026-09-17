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

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : STOCKAGE NEXTCLOUD OK")


if __name__ == "__main__":
    main()
