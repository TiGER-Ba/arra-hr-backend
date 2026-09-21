"""Sauvegarde PostgreSQL : confinement, rotation, refus des dumps douteux.

Deux propriétés sont critiques ici, parce qu'elles portent sur des **données
qu'on ne peut pas récupérer** :

1. la rotation **supprime** des fichiers sur un drive partagé — elle ne doit
   jamais toucher autre chose que ses propres sauvegardes ;
2. un dump tronqué ne doit **jamais** être déposé : il déclencherait la
   rotation et ferait tomber une sauvegarde saine hors de la fenêtre.

Et le dossier des sauvegardes doit rester distinct de celui des documents : le
dump contient les salaires, les CIN et les RIB en clair.
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


def refuse(fn, libelle, attendu=""):
    try:
        fn()
        verifier(False, libelle, "aucune erreur levée")
    except Exception as e:  # noqa: BLE001
        ok = attendu.lower() in str(e).lower() if attendu else True
        verifier(ok, libelle, str(e)[:90])


class _FauxDrive:
    """Nextcloud en mémoire, limité à ce que la sauvegarde utilise."""

    def __init__(self, fichiers=()):
        self.fichiers = {n: b"x" for n in fichiers}
        self.supprimes = []

    def lister(self, cfg, chemin=""):
        return [{"nom": n, "dossier": False, "taille": len(c), "modifie": None}
                for n, c in self.fichiers.items()]

    def envoyer(self, cfg, chemin, contenu, type_mime=None):
        self.fichiers[chemin] = contenu
        return chemin

    def supprimer(self, cfg, chemin):
        self.supprimes.append(chemin)
        self.fichiers.pop(chemin, None)

    def assurer_dossier(self, cfg, chemin=""):
        return False

    def installer(self, cfg):
        """Remplace les fonctions du module et rend un restaurateur."""
        from app.services import nextcloud as nc

        origines = (nc.lister, nc.envoyer, nc.supprimer, nc.assurer_dossier, nc.config)
        nc.lister, nc.envoyer = self.lister, self.envoyer
        nc.supprimer, nc.assurer_dossier = self.supprimer, self.assurer_dossier
        nc.config = lambda db, obligatoire=True: cfg

        def restaurer():
            (nc.lister, nc.envoyer, nc.supprimer,
             nc.assurer_dossier, nc.config) = origines
        return restaurer


def main():
    from app.services import nextcloud as nc
    from app import sauvegarde as sv

    doc = nc.Config(url="https://x", utilisateur="svc", mot_de_passe="p",
                    racine="6.10 RH Admin web")
    sauv = nc.Config(url="https://x", utilisateur="svc", mot_de_passe="p",
                     racine=sv.RACINE_DEFAUT)

    print("\n— 1. Le dossier des sauvegardes est SÉPARÉ des documents —")
    # ⚠️ Le dump contient salaires, CIN et RIB en clair : il ne doit pas
    # atterrir dans le dossier que l'équipe RH parcourt au quotidien.
    verifier(sv.RACINE_DEFAUT != doc.racine,
             "racine distincte de la racine documentaire", sv.RACINE_DEFAUT)
    verifier("6.11" in sv.RACINE_DEFAUT, "dossier 6.11 attendu", sv.RACINE_DEFAUT)

    print("\n— 2. Le confinement s'applique aussi aux sauvegardes —")
    # Même garde-fou que pour les documents : on ne sort pas de sa racine.
    resolu = nc._chemin_sur(sauv, "arra-admin_20260921-030000.sql.gz")
    verifier(resolu.startswith(sv.RACINE_DEFAUT + "/"),
             "un chemin normal reste sous 6.11", resolu)
    for mauvais in ("../6.10 RH Admin web/vol.sql.gz",
                    "../../6.9 Recrutement/vol.sql.gz",
                    "/etc/passwd"):
        refuse(lambda m=mauvais: nc._chemin_sur(sauv, m), f"refusé : {mauvais!r}")

    print("\n— 3. Le nom est strict et triable —")
    nom = sv.nom_sauvegarde()
    verifier(sv.MOTIF_SAUVEGARDE.match(nom), "le nom généré correspond au motif", nom)
    for mauvais in ("dump.sql.gz", "arra-admin_2026.sql.gz",
                    "arra-admin_20260921-030000.sql", "../evasion.sql.gz"):
        verifier(not sv.MOTIF_SAUVEGARDE.match(mauvais), f"rejeté : {mauvais!r}")

    print("\n— 4. Un dump tronqué n'est PAS déposé —")
    # Le déposer déclencherait la rotation et ferait tomber une sauvegarde
    # saine hors de la fenêtre des 7 jours.
    refuse(lambda: sv.deposer(b"court", sv.nom_sauvegarde()),
           "dump de 5 octets refusé", "suspect")
    refuse(lambda: sv.deposer(b"", sv.nom_sauvegarde()),
           "dump vide refusé", "suspect")
    refuse(lambda: sv.deposer(b"z" * 5000, "dump.sql.gz"),
           "nom non conforme refusé", "non conforme")

    print("\n— 5. Rotation : 7 SAUVEGARDES conservées, pièces comprises —")
    # ⚠️ Une sauvegarde = deux pièces. Compter des fichiers au lieu de
    # sauvegardes ne garderait que 3 jours et demi.
    anciennes = [f"arra-admin_202609{j:02d}-030000{suf}"
                 for j in range(1, 11)
                 for suf in (".sql.gz", "_fichiers.tar.gz")]
    drive = _FauxDrive(anciennes)
    restaurer = drive.installer(sauv)
    try:
        supprimees = sv._rotation(sauv)
    finally:
        restaurer()
    restants = sv._par_horodatage(list(drive.fichiers))
    verifier(len(restants) == sv.RETENTION,
             f"{sv.RETENTION} sauvegardes conservées", str(len(restants)))
    verifier(len(drive.fichiers) == sv.RETENTION * 2,
             "soit 14 pièces — les paires ne sont pas cassées", str(len(drive.fichiers)))
    verifier(all(len(p) == 2 for p in restants.values()),
             "chaque sauvegarde conservée garde SES DEUX pièces")
    verifier(sorted(supprimees) == sorted(anciennes[:6]),
             "les 3 plus anciennes partent en entier (6 pièces)", str(len(supprimees)))
    verifier("arra-admin_20260910-030000.sql.gz" in drive.fichiers
             and "arra-admin_20260910-030000_fichiers.tar.gz" in drive.fichiers,
             "la plus récente est conservée, ses deux pièces incluses")

    print("\n— 6. ⚠️ La rotation ne touche JAMAIS un fichier étranger —")
    # Le scénario redouté : le dossier contient autre chose — un dump déposé à
    # la main, une archive d'un autre outil — et la rotation l'efface.
    etrangers = [
        "backup_mysql_recrutement.sql.gz",   # l'autre plateforme
        "arra-admin_IMPORTANT.sql.gz",       # nom proche mais non conforme
        "avant-restauration_20260101-000000.sql.gz",
        "NOTES.txt",
    ]
    drive = _FauxDrive(anciennes + etrangers)
    restaurer = drive.installer(sauv)
    try:
        sv._rotation(sauv)
    finally:
        restaurer()
    for e in etrangers:
        verifier(e in drive.fichiers, f"intact : {e}")
    verifier(not any(e in drive.supprimes for e in etrangers),
             "aucun fichier étranger supprimé", str(drive.supprimes))

    print("\n— 7. Un dépôt valide passe et purge derrière lui —")
    drive = _FauxDrive(anciennes)
    restaurer = drive.installer(sauv)
    try:
        nom = sv.nom_sauvegarde()
        # `deposer` ouvre sa propre session : on court-circuite la config.
        import app.sauvegarde as module
        origine = module.config_sauvegardes
        module.config_sauvegardes = lambda db: sauv
        try:
            compte_rendu = sv.deposer(b"g" * 4096, nom)
        finally:
            module.config_sauvegardes = origine
    finally:
        restaurer()
    verifier(compte_rendu["depose"] == nom, "la sauvegarde est déposée")
    verifier(compte_rendu["sauvegardes_conservees"] == sv.RETENTION,
             "la rétention est appliquée après dépôt",
             str(compte_rendu["sauvegardes_conservees"]))
    verifier(nom in compte_rendu["conservees"], "la nouvelle est dans les conservées")

    print("\n— 7bis. L'archive des fichiers capture bien uploads/ —")
    # ⚠️ C'est la pièce qui protège la signature et le cachet scannés : des
    # ORIGINAUX numérisés, que rien ne reconstruit s'ils disparaissent.
    import io as _io
    import tarfile
    tampon = _io.BytesIO()
    bilan = sv.archiver_fichiers(tampon)
    verifier(tampon.tell() > 0, "une archive non vide est produite")
    tampon.seek(0)
    with tarfile.open(fileobj=tampon, mode="r|gz") as t:
        membres = [m.name for m in t]
    verifier(all(m.startswith("uploads/") for m in membres),
             "tous les chemins sont sous uploads/", str(membres[:2]))
    verifier(not any(chr(92) in m for m in membres),
             "aucun antislash dans les chemins de l'archive")
    verifier(bilan["fichiers"] == len(membres),
             "le compte rendu correspond au contenu réel",
             f"{bilan['fichiers']} vs {len(membres)}")

    print("\n— 7ter. Les deux pièces partagent l'horodatage —")
    from datetime import datetime, timezone
    instant = datetime(2026, 9, 21, 3, 0, 0, tzinfo=timezone.utc)
    a, b = sv.nom_sauvegarde(instant), sv.nom_sauvegarde(instant, fichiers=True)
    ha = sv.MOTIF_SAUVEGARDE.match(a).group("horodatage")
    hb = sv.MOTIF_SAUVEGARDE.match(b).group("horodatage")
    verifier(ha == hb, "même horodatage pour la base et les fichiers", f"{ha} / {hb}")
    verifier(a.endswith(".sql.gz") and b.endswith("_fichiers.tar.gz"),
             "les deux genres sont distincts")

    print("\n— 7quater. Aller-retour : une signature effacée revient —")
    # Le scénario qui justifie cette pièce : le cachet disparaît, et aucune
    # restauration de base ne le ramène.
    import shutil, tempfile
    from app.config import settings
    memoire_uploads = settings.UPLOADS_DIR
    bac = tempfile.mkdtemp()
    try:
        settings.UPLOADS_DIR = os.path.join(bac, "uploads")
        os.makedirs(os.path.join(settings.UPLOADS_DIR, "parametrage"))
        signature = os.path.join(settings.UPLOADS_DIR, "parametrage", "signature.png")
        with open(signature, "wb") as f:
            f.write(b"ORIGINAL-SCANNE" * 40)

        paquet = _io.BytesIO()
        sv.archiver_fichiers(paquet)
        shutil.rmtree(settings.UPLOADS_DIR)          # la perte
        verifier(not os.path.exists(signature), "la signature a bien disparu")

        paquet.seek(0)
        bilan = sv.extraire_fichiers(paquet)
        verifier(os.path.exists(signature), "elle est restaurée")
        with open(signature, "rb") as f:
            verifier(f.read() == b"ORIGINAL-SCANNE" * 40, "octet pour octet")
        verifier(bilan["ignores"] == 0, "aucune entrée écartée sur une archive saine")

        print("\n— 7quinquies. ⚠️ Une archive PIÉGÉE n'écrit rien dehors —")
        # L'archive vient du réseau : une entrée « ../ » ou un chemin absolu
        # écrirait hors du dossier. Et elle doit être SIGNALÉE, pas avalée en
        # silence — sinon personne ne saurait que l'archive est corrompue.
        import tarfile as _tar
        piege = _io.BytesIO()
        with _tar.open(fileobj=piege, mode="w|gz") as t:
            for nom, contenu in (("../../../evasion.txt", b"boom"),
                                 ("/etc/passwd", b"boom"),
                                 ("uploads/../../dehors.txt", b"boom"),
                                 ("uploads/legitime.txt", b"ok")):
                info = _tar.TarInfo(nom)
                info.size = len(contenu)
                t.addfile(info, _io.BytesIO(contenu))
        piege.seek(0)
        bilan = sv.extraire_fichiers(piege)
        verifier(bilan["ignores"] == 3, "les 3 entrées piégées sont écartées",
                 str(bilan))
        verifier(bilan["extraits"] == 1, "seule l'entrée légitime passe")
        verifier(os.path.exists(os.path.join(settings.UPLOADS_DIR, "legitime.txt")),
                 "le fichier légitime est bien écrit")
        verifier(not os.path.exists(os.path.join(bac, "dehors.txt")),
                 "rien n'est écrit hors du dossier uploads")
    finally:
        settings.UPLOADS_DIR = memoire_uploads
        shutil.rmtree(bac, ignore_errors=True)

    print("\n— 8. Les scripts n'approchent jamais le recrutement —")
    # ⚠️ On inspecte les COMMANDES, pas la prose : ces scripts *mentionnent*
    # volontairement « jamais MySQL », « jamais docker system prune » dans
    # leurs avertissements. Chercher les mots dans le fichier entier ferait
    # échouer le test sur ses propres garde-fous.
    racine_deploy = os.path.join("deploy")
    for fichier in ("backup.sh", "restaurer.sh"):
        brut = open(os.path.join(racine_deploy, fichier), encoding="utf-8").read()
        code = "\n".join(
            ligne for ligne in brut.splitlines()
            if ligne.strip() and not ligne.lstrip().startswith("#")
        ).lower()
        verifier('-p "$projet"' in code or "-p arra-admin" in code,
                 f"{fichier} : projet compose explicite")
        for interdit in ("arra-rh", "mysql", "system prune", "volume prune",
                         "compose down", "down -v"):
            verifier(interdit not in code, f"{fichier} : aucun « {interdit} » exécuté")

    print("\n— 9. update.sh prend un point de retour avant migration —")
    texte = open(os.path.join(racine_deploy, "update.sh"), encoding="utf-8").read()
    verifier("backup.sh" in texte, "update.sh appelle la sauvegarde")
    verifier("SANS point de retour" in texte,
             "l'échec de sauvegarde est signalé sans bloquer le déploiement")

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : SAUVEGARDES OK")


if __name__ == "__main__":
    main()
