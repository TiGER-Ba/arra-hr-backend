"""Numérotation des matricules ARRA-I### / ARRA-E### et migration des anciens.

Vérifie les trois règles arrêtées :
  1. la lettre vient du contrat (freelance = E, tout le reste = I) ;
  2. le compteur est PARTAGÉ — E001, I002, I003, E004 est une suite valide ;
  3. la renumérotation des anciens EMP### préserve l'ordre d'ancienneté,
     ne crée pas de doublon, et ne fait rien au second passage.
"""
import os
import re
import sys
import tempfile
from datetime import date

os.environ.setdefault("ENVIRONMENT", "development")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def main():
    fichier = os.path.join(tempfile.mkdtemp(), "matricules.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{fichier}"

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    import app.database as database
    engine = create_engine(os.environ["DATABASE_URL"])
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine)

    import app.db_migrate as db_migrate
    db_migrate.engine = engine

    from app.database import Base
    import app.models  # noqa: F401  (enregistre toutes les tables)
    from app.models.employee import Employe
    from app.models.user import Utilisateur
    from app.routers.users import generate_matricule, lettre_matricule

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    def creer(nom, contrat, matricule=None):
        u = Utilisateur(nom=nom, email=f"{nom.lower()}@test.ma",
                        mot_de_passe="x", role="employe")
        db.add(u)
        db.flush()
        e = Employe(
            utilisateur_id=u.id,
            matricule=matricule or generate_matricule(db, contrat),
            poste="Ingénieur", departement="Informatique",
            salaire_base=10000, date_embauche=date(2024, 1, 1),
            type_contrat=contrat,
        )
        db.add(e)
        db.commit()
        return e

    print("\n— 1. La lettre suit le type de contrat —")
    verifier(lettre_matricule("Freelance") == "E", "freelance → E")
    verifier(lettre_matricule("freelance") == "E", "casse ignorée")
    verifier(lettre_matricule("CDI") == "I", "CDI → I")
    verifier(lettre_matricule("CDD") == "I", "CDD → I")
    verifier(lettre_matricule("Stage") == "I", "Stage → I")
    verifier(lettre_matricule(None) == "I", "contrat absent → I par défaut")

    print("\n— 2. Compteur partagé entre internes et externes —")
    attendus = [
        ("Alpha", "Freelance", "ARRA-E001"),
        ("Bravo", "CDI", "ARRA-I002"),
        ("Charlie", "Stage", "ARRA-I003"),
        ("Delta", "Freelance", "ARRA-E004"),
    ]
    for nom, contrat, attendu in attendus:
        e = creer(nom, contrat)
        verifier(e.matricule == attendu,
                 f"{nom} ({contrat}) → {attendu}", f"obtenu {e.matricule}")

    print("\n— 3. Le matricule ne change pas avec le contrat —")
    alpha = db.query(Employe).filter_by(matricule="ARRA-E001").first()
    alpha.type_contrat = "CDI"
    db.commit()
    db.refresh(alpha)
    verifier(alpha.matricule == "ARRA-E001",
             "freelance passé en CDI garde son ARRA-E001", alpha.matricule)

    print("\n— 4. Migration des anciens EMP### —")
    # Base repartie de zéro, peuplée à l'ancien format
    db.query(Employe).delete()
    db.query(Utilisateur).delete()
    db.commit()
    creer("Ancien1", "CDI", matricule="EMP001")
    creer("Ancien2", "Freelance", matricule="EMP002")
    creer("Ancien3", "CDD", matricule="EMP007")   # trou dans la numérotation

    db_migrate._migrer_matricules()

    with engine.begin() as conn:
        apres = conn.execute(text(
            "SELECT matricule, type_contrat FROM employes ORDER BY id"
        )).fetchall()
    obtenus = [m for m, _ in apres]
    verifier(obtenus == ["ARRA-I001", "ARRA-E002", "ARRA-I003"],
             "renumérotation dans l'ordre d'ancienneté, lettre selon le contrat",
             str(obtenus))
    verifier(all(re.match(r"^ARRA-[IE]\d{3}$", m) for m in obtenus),
             "tous au format ARRA-{I|E}###")
    verifier(len(set(obtenus)) == len(obtenus), "aucun doublon")

    print("\n— 5. Migration rejouée : rien ne bouge —")
    db_migrate._migrer_matricules()
    with engine.begin() as conn:
        rejoue = [m for (m,) in conn.execute(text(
            "SELECT matricule FROM employes ORDER BY id")).fetchall()]
    verifier(rejoue == obtenus, "second passage sans effet", str(rejoue))

    print("\n— 6. Le compteur repart au-dessus de l'existant —")
    suivant = generate_matricule(db, "CDI")
    verifier(suivant == "ARRA-I004",
             "après ARRA-I003, le suivant est ARRA-I004", suivant)

    print("\n— 7. Mélange ancien et nouveau format sans collision —")
    creer("Mixte", "CDI", matricule="EMP042")
    db_migrate._migrer_matricules()
    with engine.begin() as conn:
        final = [m for (m,) in conn.execute(text(
            "SELECT matricule FROM employes ORDER BY id")).fetchall()]
    verifier(len(set(final)) == len(final), "toujours aucun doublon", str(final))
    verifier(final[-1] == "ARRA-I004",
             "l'ancien isolé prend le numéro libre suivant", final[-1])

    db.close()

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS))
        sys.exit(1)
    print("RESULTAT : MATRICULES OK")


if __name__ == "__main__":
    main()
