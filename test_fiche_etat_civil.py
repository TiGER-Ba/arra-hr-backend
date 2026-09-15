"""État civil de la fiche salarié : naissance, sexe, nationalité, retraite.

Couvre aussi la RÉGRESSION qui motivait ce lot : le formulaire de création
n'envoyait pas `nombre_enfants`. Un salarié déclaré marié était donc refusé par
le serveur — avec 0 enfant comme avec 3 — alors que le formulaire semblait
complet. Le test vérifie ici le contrat serveur : marié + 0 doit passer.
"""
import os
import sys
import tempfile
from datetime import date, timedelta

os.environ.setdefault("ENVIRONMENT", "development")

ECHECS = []


def verifier(condition, libelle, detail=""):
    if condition:
        print(f"  OK    {libelle}")
    else:
        print(f"  ECHEC {libelle}" + (f" — {detail}" if detail else ""))
        ECHECS.append(libelle)


def main():
    fichier = os.path.join(tempfile.mkdtemp(), "etatcivil.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{fichier}"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.database as database
    engine = create_engine(os.environ["DATABASE_URL"])
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine)

    from fastapi import HTTPException

    from app.database import Base
    import app.models  # noqa: F401
    from app.models.referentiel import (
        CATEGORIE_NATIONALITE, NATIONALITE_DEFAUT, ValeurReferentiel,
    )
    from app.routers.users import (
        AGE_MIN, VALID_SEXES, champs_requis,
        _resoudre_enfants, _valeurs_referentiel, _valider_dates, _valider_enum, assurer_valeur,
    )

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    def refuse(fn, *a):
        try:
            fn(*a)
            return None
        except HTTPException as e:
            return e.detail

    aujourdhui = date.today()
    naissance = aujourdhui - timedelta(days=int(30 * 365.25))   # 30 ans

    print("\n— 1. RÉGRESSION : marié avec 0 enfant doit passer —")
    verifier(_resoudre_enfants("Marié(e)", 0) == 0,
             "marié + 0 enfant accepté côté serveur", str(_resoudre_enfants("Marié(e)", 0)))
    verifier(refuse(_resoudre_enfants, "Marié(e)", None) is not None,
             "marié sans valeur transmise → refusé (ce que produisait le bug)")

    print("\n— 2. Champs exigés de la fiche —")
    # L'état civil vaut pour les deux natures, salarié comme externe
    for contrat in ("CDI", "Freelance"):
        requis = {champ for champ, _ in champs_requis(contrat)}
        for champ in ("date_naissance", "sexe", "nationalite"):
            verifier(champ in requis, f"{champ} est obligatoire ({contrat})")
        for champ in ("numero_retraite", "date_premiere_experience"):
            verifier(champ not in requis, f"{champ} reste facultatif ({contrat})")

    print("\n— 3. Sexe —")
    verifier(_valider_enum("M", VALID_SEXES, "Sexe") == "M", "M accepté")
    verifier(_valider_enum("F", VALID_SEXES, "Sexe") == "F", "F accepté")
    verifier(_valider_enum(None, VALID_SEXES, "Sexe") is None, "absent → pas de refus ici")
    verifier(refuse(_valider_enum, "X", VALID_SEXES, "Sexe") is not None, "valeur inconnue refusée")

    print("\n— 4. Dates : bornes de vraisemblance —")
    verifier(refuse(_valider_dates, naissance, None, None) is None,
             "naissance à 30 ans acceptée")
    trop_jeune = aujourdhui - timedelta(days=int(5 * 365.25))
    verifier(refuse(_valider_dates, trop_jeune, None, None) is not None,
             f"naissance il y a 5 ans refusée (< {AGE_MIN} ans)")
    futur = aujourdhui + timedelta(days=365)
    verifier(refuse(_valider_dates, futur, None, None) is not None,
             "naissance dans le futur refusée")
    verifier(refuse(_valider_dates, aujourdhui - timedelta(days=int(150 * 365.25)), None, None) is not None,
             "naissance il y a 150 ans refusée")

    print("\n— 5. Première expérience —")
    premiere = naissance + timedelta(days=int(22 * 365.25))     # à 22 ans
    verifier(refuse(_valider_dates, naissance, premiere, None) is None,
             "première expérience à 22 ans acceptée")
    verifier(refuse(_valider_dates, naissance, aujourdhui + timedelta(days=30), None) is not None,
             "première expérience dans le futur refusée")
    enfance = naissance + timedelta(days=int(8 * 365.25))       # à 8 ans
    verifier(refuse(_valider_dates, naissance, enfance, None) is not None,
             "première expérience avant l'âge de travail refusée")
    verifier(refuse(_valider_dates, None, premiere, None) is None,
             "première expérience seule, sans naissance connue : acceptée")

    print("\n— 6. Référentiel des nationalités —")
    valeurs = _valeurs_referentiel(db, CATEGORIE_NATIONALITE)
    verifier(NATIONALITE_DEFAUT in valeurs,
             f"« {NATIONALITE_DEFAUT} » proposée d'office", str(valeurs[:3]))
    verifier(len(valeurs) > 1, "la liste initiale contient plusieurs entrées", str(len(valeurs)))

    avant = len(valeurs)
    assurer_valeur(db, CATEGORIE_NATIONALITE, "Portugaise")
    db.commit()
    verifier(len(_valeurs_referentiel(db, CATEGORIE_NATIONALITE)) == avant + 1,
             "une nouvelle valeur est ajoutée")

    assurer_valeur(db, CATEGORIE_NATIONALITE, "portugaise")
    db.commit()
    verifier(len(_valeurs_referentiel(db, CATEGORIE_NATIONALITE)) == avant + 1,
             "un doublon de casse n'est pas ajouté deux fois")

    assurer_valeur(db, CATEGORIE_NATIONALITE, "   ")
    db.commit()
    verifier(len(_valeurs_referentiel(db, CATEGORIE_NATIONALITE)) == avant + 1,
             "une valeur vide est ignorée")

    verifier(
        db.query(ValeurReferentiel).filter(
            ValeurReferentiel.categorie == CATEGORIE_NATIONALITE
        ).count() == avant + 1,
        "aucun doublon en base",
    )

    print("\n— 7. La liste initiale n'est semée qu'une fois —")
    rappel = _valeurs_referentiel(db, CATEGORIE_NATIONALITE)
    verifier(len(rappel) == avant + 1, "second appel : rien de re-semé", str(len(rappel)))

    db.close()

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS))
        sys.exit(1)
    print("RESULTAT : ETAT CIVIL OK")


if __name__ == "__main__":
    main()
