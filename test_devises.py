"""Devise déduite de l'entité : dirhams au Maroc, euros en France.

Vérifie :
  1. la résolution de la devise et le formatage ;
  2. l'absence d'addition entre entités (la masse salariale est ventilée) ;
  3. la devise des soldes monétaires à la création ;
  4. le refus du bulletin de paie pour un salarié France — le barème codé dans
     le template est marocain (CNSS, AMO, IR) : en changer la seule devise
     produirait un document faux.
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


def main():
    fichier = os.path.join(tempfile.mkdtemp(), "devises.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{fichier}"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.database as database
    engine = create_engine(os.environ["DATABASE_URL"])
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine)

    from app.database import Base
    import app.models  # noqa: F401
    from app.models.employee import Employe
    from app.models.solde import SoldeEmploye
    from app.models.user import Utilisateur
    from app.services.devises import (
        devise_employe, devise_entite, formater_montant, symbole, totaliser_par_entite,
    )
    from app.services.soldes import initialiser_soldes_par_defaut

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    print("\n— 1. Devise selon l'entité —")
    verifier(devise_entite("MA") == "MAD", "Maroc → MAD")
    verifier(devise_entite("FR") == "EUR", "France → EUR")
    verifier(devise_entite("fr") == "EUR", "casse ignorée")
    verifier(devise_entite(None) == "MAD", "entité absente → MAD par défaut")
    verifier(devise_entite("XX") == "MAD", "entité inconnue → MAD par défaut")
    verifier(symbole("EUR") == "€", "symbole de l'euro")
    verifier(symbole("MAD") == "MAD", "symbole du dirham")

    print("\n— 2. Formatage des montants —")
    verifier(formater_montant(9500, "MA") == "9 500,00 MAD",
             "9 500,00 MAD", formater_montant(9500, "MA"))
    verifier(formater_montant(3200.5, "FR") == "3 200,50 €",
             "3 200,50 €", formater_montant(3200.5, "FR"))
    verifier(formater_montant(None, "FR") == "—", "montant absent → tiret")

    def creer(nom, entite, salaire):
        u = Utilisateur(nom=nom, email=f"{nom.lower()}@test.ma",
                        mot_de_passe="x", role="employe")
        db.add(u)
        db.flush()
        e = Employe(
            utilisateur_id=u.id, matricule=f"ARRA-I{u.id:03d}",
            poste="Ingénieur", departement="Informatique",
            salaire_base=salaire, date_embauche=date(2024, 1, 1),
            type_contrat="CDI", entite=entite, statut="actif",
        )
        db.add(e)
        db.commit()
        return e

    maroc1 = creer("Alpha", "MA", 9500)
    maroc2 = creer("Bravo", "MA", 11000)
    france = creer("Charlie", "FR", 3200)

    print("\n— 3. Devise d'un salarié —")
    verifier(devise_employe(maroc1) == "MAD", "salarié Maroc → MAD")
    verifier(devise_employe(france) == "EUR", "salarié France → EUR")

    print("\n— 4. Masse salariale ventilée, jamais additionnée —")
    totaux = totaliser_par_entite([maroc1, maroc2, france])
    par_entite = {t["entite"]: t for t in totaux}
    verifier(len(totaux) == 2, "une ligne par entité", str(totaux))
    verifier(par_entite["MA"]["total"] == 20500 and par_entite["MA"]["devise"] == "MAD",
             "Maroc : 20 500 MAD", str(par_entite.get("MA")))
    verifier(par_entite["FR"]["total"] == 3200 and par_entite["FR"]["devise"] == "EUR",
             "France : 3 200 EUR", str(par_entite.get("FR")))
    verifier(par_entite["MA"]["effectif"] == 2 and par_entite["FR"]["effectif"] == 1,
             "effectifs corrects par entité")
    somme_brute = sum(t["total"] for t in totaux)
    verifier(somme_brute != par_entite["MA"]["total"],
             "aucun total global unique n'est proposé (MAD + EUR resterait faux)")

    verifier(totaliser_par_entite([maroc1])[0]["entite"] == "MA",
             "une seule entité → une seule ligne")
    verifier(totaliser_par_entite([]) == [], "aucun salarié → liste vide")

    print("\n— 5. Devise des soldes monétaires —")
    initialiser_soldes_par_defaut(db, maroc1.id)
    initialiser_soldes_par_defaut(db, france.id)

    def unite(emp_id, type_solde):
        s = db.query(SoldeEmploye).filter_by(employe_id=emp_id, type=type_solde).first()
        return s.unite if s else None

    verifier(unite(maroc1.id, "avance_salaire_plafond") == "MAD",
             "plafond d'avance Maroc en MAD", str(unite(maroc1.id, "avance_salaire_plafond")))
    verifier(unite(france.id, "avance_salaire_plafond") == "EUR",
             "plafond d'avance France en EUR", str(unite(france.id, "avance_salaire_plafond")))
    verifier(unite(france.id, "conge_annuel") == "jours",
             "les congés restent en jours dans les deux entités")

    print("\n— 6. Libellé monétaire du formulaire de demande —")
    from app.services.demande_service import champs_meta
    labels_ma = {c["name"]: c["label"] for c in champs_meta("demande_avance_salaire", "MAD")}
    labels_fr = {c["name"]: c["label"] for c in champs_meta("demande_avance_salaire", "EUR")}
    verifier(labels_ma["montant"] == "Montant (MAD)", "Maroc : Montant (MAD)", labels_ma["montant"])
    verifier(labels_fr["montant"] == "Montant (EUR)", "France : Montant (EUR)", labels_fr["montant"])
    verifier("{devise}" not in labels_fr["montant"], "le gabarit est bien substitué")

    print("\n— 7. Bulletin de paie : barème marocain, refusé pour la France —")
    import io as _io
    from pathlib import Path
    gabarit = Path("app/templates/bulletin_paie.html").read_text(encoding="utf-8")
    verifier("CNSS" in gabarit and "AMO" in gabarit,
             "le template porte bien des cotisations marocaines")
    verifier("{{ devise }}" in gabarit,
             "la colonne des montants utilise la devise du salarié")

    from app.models.demande import Demande
    from app.models.template import Template as TemplateModel
    from app.services import pdf_generator

    db.add(TemplateModel(type="bulletin_paie", nom="Bulletin",
                         contenu_html="<p>{{ devise }}</p>"))
    d = Demande(employe_id=france.id, type="bulletin_paie", statut="validee",
                donnees_collectees={"mois": "01", "annee": 2026})
    db.add(d)
    db.commit()

    refus = None
    try:
        pdf_generator._load_base_data(db, d.id)
    except RuntimeError as e:
        refus = str(e)
    # ⚠️ Le refus ne tient plus au barème français : l'application ne génère
    # AUCUN bulletin, quelle que soit l'entité — il est établi par le
    # comptable (cf. test_bulletin_depot.py). Le refus vaut donc toujours pour
    # un salarié France, pour une raison plus large.
    verifier(refus is not None, "bulletin refusé pour un salarié France")
    verifier(refus is not None and "comptable" in refus.lower(),
             "le refus dit d'où vient le document", refus or "aucun message")

    # Le refus ne dépend PAS de l'entité : un bulletin marocain non plus n'est
    # pas généré. Le vérifier ici évite qu'on croie la règle limitée à la France.
    d_ma = Demande(employe_id=maroc1.id, type="bulletin_paie", statut="validee",
                   donnees_collectees={"mois": "01", "annee": 2026})
    db.add(d_ma)
    db.commit()
    refus_ma = None
    try:
        pdf_generator._load_base_data(db, d_ma.id)
    except RuntimeError as e:
        refus_ma = str(e)
    verifier(refus_ma is not None, "bulletin refusé aussi pour un salarié Maroc")
    # La devise des autres documents est vérifiée en section 8.

    print("\n— 8. Les autres documents portent la devise du salarié —")
    for type_doc in ("attestation_salaire", "demande_avance_salaire"):
        db.add(TemplateModel(type=type_doc, nom=type_doc, contenu_html="<p>{{ devise }}</p>"))
    db.commit()
    for type_doc, emp, attendu in (
        ("attestation_salaire", france, "€"),
        ("attestation_salaire", maroc1, "MAD"),
    ):
        dd = Demande(employe_id=emp.id, type=type_doc, statut="validee", donnees_collectees={})
        db.add(dd)
        db.commit()
        _, _, dn = pdf_generator._load_base_data(db, dd.id)
        verifier(dn["devise"] == attendu,
                 f"{type_doc} / {emp.entite} → {attendu}", str(dn["devise"]))

    _ = _io  # silence linters

    db.close()

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS))
        sys.exit(1)
    print("RESULTAT : DEVISES OK")


if __name__ == "__main__":
    main()
