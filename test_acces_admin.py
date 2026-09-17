"""L'administrateur peut tout ce que peut le RH.

Le rôle `admin` passait bien `require_rh`, mais plusieurs actions traçaient leur
auteur via la table `rh` — dont **aucune ligne n'était créée pour un admin**.
`_get_rh()` répondait alors 404 « Profil RH introuvable » : l'admin ne pouvait ni
valider une demande, ni déposer une pièce dans le dépôt d'un salarié.

Ce test vérifie que le profil est résolu pour les deux rôles, et qu'il n'est
jamais créé en double.
"""
import os
import sys

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:///./test_acces_admin.db")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def main():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    import app.models  # noqa: F401  — enregistre toutes les tables
    from app.models.rh import RH
    from app.models.user import Utilisateur
    from app.services.profil import profil_rh

    fichier = "test_acces_admin.db"
    if os.path.exists(fichier):
        os.remove(fichier)

    moteur = create_engine(f"sqlite:///./{fichier}")
    Base.metadata.create_all(moteur)
    db = sessionmaker(bind=moteur)()

    admin = Utilisateur(nom="Directrice", email="dir@arra.ma",
                        mot_de_passe="x", role="admin")
    rh_user = Utilisateur(nom="Gestionnaire RH", email="grh@arra.ma",
                          mot_de_passe="x", role="rh")
    db.add_all([admin, rh_user])
    db.commit()

    print("\n— 1. L'admin obtient un profil RH au lieu d'un 404 —")
    profil = profil_rh(admin, db)
    db.commit()
    verifier(profil is not None and profil.id is not None,
             "profil créé à la demande pour l'admin")
    verifier(profil.utilisateur_id == admin.id,
             "le profil est bien rattaché à l'admin")
    verifier(profil.service == "Direction",
             "service par défaut lisible dans l'audit", profil.service)

    print("\n— 2. Le profil sert à tracer l'auteur, donc il doit être stable —")
    encore = profil_rh(admin, db)
    db.commit()
    verifier(encore.id == profil.id, "deuxième appel : même profil, pas de doublon",
             f"{profil.id} → {encore.id}")
    verifier(db.query(RH).filter(RH.utilisateur_id == admin.id).count() == 1,
             "une seule ligne rh pour l'admin")

    print("\n— 3. Le RH garde le comportement d'avant —")
    existant = RH(utilisateur_id=rh_user.id, service="Paie")
    db.add(existant)
    db.commit()
    retrouve = profil_rh(rh_user, db)
    verifier(retrouve.id == existant.id, "le profil RH existant est réutilisé")
    verifier(retrouve.service == "Paie", "son service n'est pas écrasé", retrouve.service)

    print("\n— 4. Les deux rôles ont des profils distincts —")
    verifier(profil_rh(admin, db).id != profil_rh(rh_user, db).id,
             "admin et RH ne partagent pas la même ligne")

    db.close()
    moteur.dispose()
    if os.path.exists(fichier):
        os.remove(fichier)

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS[:4]))
        sys.exit(1)
    print("RESULTAT : ACCES ADMIN OK")


if __name__ == "__main__":
    main()
