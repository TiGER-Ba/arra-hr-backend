"""Externes (freelance, prestataire) face aux salariés (CDI, CDD, Stage).

Un externe est facturé au TJM : il n'a ni salaire, ni situation familiale, ni
CNSS. La nature n'est pas stockée — elle se déduit du type de contrat, ce qui
évite qu'une colonne « externe » et le contrat se contredisent.

Vérifie aussi que la numérotation convenue tient : ARRA-E### pour un externe,
ARRA-I### pour un salarié, sur un compteur PARTAGÉ.
"""
import os
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


class Payload:
    """Imite un UserCreate, sans dépendre de Pydantic."""

    def __init__(self, **kw):
        defaults = dict(
            type_contrat="CDI", salaire_base=9000, tjm=None,
            situation_familiale="Célibataire", nombre_enfants=None, cnss="154028391",
            poste="Ingénieur", departement="Informatique", date_embauche=date(2024, 1, 1),
            entite="MA", date_naissance=date(1994, 5, 1), sexe="M",
            nationalite="Marocaine", cin="BK284571", telephone="+212 6", adresse="Casablanca",
        )
        defaults.update(kw)
        for k, v in defaults.items():
            setattr(self, k, v)


def main():
    fichier = os.path.join(tempfile.mkdtemp(), "externes.db")
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
    from app.models.employee import Employe
    from app.models.user import Utilisateur
    from app.routers.users import (
        CONTRATS_EXTERNES, CONTRATS_INTERNES, VALID_CONTRATS,
        _champs_selon_nature, _valider_fiche, champs_requis,
        est_externe, generate_matricule, lettre_matricule,
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

    print("\n— 1. Nature déduite du contrat —")
    for c in CONTRATS_EXTERNES:
        verifier(est_externe(c), f"{c} → externe")
    for c in CONTRATS_INTERNES:
        verifier(not est_externe(c), f"{c} → salarié")
    verifier(est_externe("freelance"), "casse ignorée")
    verifier(not est_externe(None), "contrat absent → salarié par défaut")
    verifier("Prestataire" in VALID_CONTRATS, "« Prestataire » est un contrat accepté")

    print("\n— 2. Champs exigés selon la nature —")
    requis_int = {c for c, _ in champs_requis("CDI")}
    requis_ext = {c for c, _ in champs_requis("Freelance")}
    verifier("salaire_base" in requis_int and "situation_familiale" in requis_int,
             "salarié : salaire et situation familiale exigés")
    verifier("tjm" in requis_ext, "externe : TJM exigé")
    verifier("salaire_base" not in requis_ext, "externe : pas de salaire demandé")
    verifier("situation_familiale" not in requis_ext, "externe : pas de situation familiale")
    verifier("poste" in requis_int and "poste" in requis_ext, "le poste reste commun")

    print("\n— 3. Un externe n'a ni salaire, ni situation familiale, ni CNSS —")
    champs = _champs_selon_nature(Payload(
        type_contrat="Freelance", tjm=2500,
        salaire_base=9000, situation_familiale="Marié(e)", nombre_enfants=3, cnss="1234",
    ))
    verifier(champs["tjm"] == 2500, "TJM conservé", str(champs["tjm"]))
    verifier(champs["salaire_base"] == 0, "salaire forcé à 0", str(champs["salaire_base"]))
    verifier(champs["situation_familiale"] is None,
             "situation familiale ignorée même si transmise")
    verifier(champs["nombre_enfants"] is None, "nombre d'enfants ignoré")
    verifier(champs["cnss"] is None, "CNSS ignoré")

    print("\n— 4. Un salarié n'a pas de TJM —")
    champs = _champs_selon_nature(Payload(type_contrat="CDI", salaire_base=9000, tjm=2500))
    verifier(champs["salaire_base"] == 9000, "salaire conservé")
    verifier(champs["tjm"] is None, "TJM ignoré même si transmis")

    print("\n— 5. Montants exigés et non nuls —")
    verifier(refuse(_champs_selon_nature, Payload(type_contrat="Freelance", tjm=None)) is not None,
             "externe sans TJM → refusé")
    verifier(refuse(_champs_selon_nature, Payload(type_contrat="Freelance", tjm=0)) is not None,
             "externe avec TJM à 0 → refusé")
    verifier(refuse(_champs_selon_nature, Payload(type_contrat="CDI", salaire_base=None)) is not None,
             "salarié sans salaire → refusé")
    verifier(refuse(_champs_selon_nature, Payload(type_contrat="CDI", salaire_base=0)) is not None,
             "salarié avec salaire à 0 → refusé")

    print("\n— 6. Le message de refus nomme la bonne nature —")
    msg = refuse(_valider_fiche, Payload(type_contrat="Freelance", tjm=None, poste=None))
    verifier(msg and "externe" in msg, "fiche externe incomplète", msg or "")
    verifier(msg and "TJM" in msg, "le TJM est cité parmi les manquants", msg or "")
    msg = refuse(_valider_fiche, Payload(type_contrat="CDI", poste=None))
    verifier(msg and "salarié" in msg, "fiche salarié incomplète", msg or "")

    print("\n— 7. Numérotation : compteur partagé, lettre selon la nature —")
    verifier(lettre_matricule("Prestataire") == "E", "Prestataire → E")
    verifier(lettre_matricule("Freelance") == "E", "Freelance → E")
    verifier(lettre_matricule("Stage") == "I", "Stage → I")

    def creer(nom, contrat):
        u = Utilisateur(nom=nom, email=f"{nom.lower()}@test.ma", mot_de_passe="x", role="employe")
        db.add(u)
        db.flush()
        e = Employe(
            utilisateur_id=u.id, matricule=generate_matricule(db, contrat),
            poste="Ingénieur", departement="Informatique",
            salaire_base=0 if est_externe(contrat) else 9000,
            tjm=2500 if est_externe(contrat) else None,
            date_embauche=date(2024, 1, 1), type_contrat=contrat,
        )
        db.add(e)
        db.commit()
        return e.matricule

    attendus = [
        ("Alpha", "Prestataire", "ARRA-E001"),
        ("Bravo", "CDI", "ARRA-I002"),
        ("Charlie", "Freelance", "ARRA-E003"),
        ("Delta", "Stage", "ARRA-I004"),
    ]
    for nom, contrat, attendu in attendus:
        obtenu = creer(nom, contrat)
        verifier(obtenu == attendu, f"{nom} ({contrat}) → {attendu}", obtenu)

    print("\n— 8. La masse salariale exclut les externes —")
    from app.services.devises import totaliser_par_entite
    tous = db.query(Employe).all()
    salaries = [e for e in tous if not est_externe(e.type_contrat)]
    totaux = totaliser_par_entite(salaries)
    verifier(len(tous) == 4 and len(salaries) == 2, "2 externes, 2 salariés", str(len(salaries)))
    verifier(totaux and totaux[0]["total"] == 18000,
             "masse salariale = 2 × 9 000, les externes exclus",
             str(totaux[0]["total"] if totaux else None))

    db.close()

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS))
        sys.exit(1)
    print("RESULTAT : EXTERNES OK")


if __name__ == "__main__":
    main()
