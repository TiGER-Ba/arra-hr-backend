"""Génération des contrats et registre de numérotation.

Vérifie que les quatre modèles produisent un PDF non vide, que le numéro suit
la règle convenue (compteur ANNUEL PARTAGÉ entre tous les types), qu'il est
attribué une seule fois par (salarié, type), et qu'une fiche incomplète est
refusée en nommant ce qui manque plutôt qu'en produisant un contrat à trous.
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
    fichier = os.path.join(tempfile.mkdtemp(), "contrats.db")
    os.environ["DATABASE_URL"] = f"sqlite:///{fichier}"

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.database as database
    engine = create_engine(os.environ["DATABASE_URL"])
    database.engine = engine
    database.SessionLocal = sessionmaker(bind=engine)

    from app.database import Base
    import app.models  # noqa: F401
    from app.models.contrat import Contrat
    from app.models.employee import Employe
    from app.models.template import Template as TemplateModel
    from app.models.user import Utilisateur
    from app.services.contrats import (
        MODELES, ContratIndisponible, attribuer_numero, generer_pdf_contrat, modele_pour,
    )

    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine)()

    # Les modèles de référence, chargés depuis le dépôt comme le fait seed.py
    for type_modele in MODELES.values():
        chemin = os.path.join("app", "templates", f"{type_modele}.html")
        with open(chemin, encoding="utf-8") as f:
            db.add(TemplateModel(type=type_modele, nom=type_modele,
                                 contenu_html=f.read(), champs_requis=[]))
    db.commit()

    def creer(nom, contrat, **kw):
        u = Utilisateur(nom=nom.upper(), prenom="Test",
                        email=f"{nom.lower()}@test.ma", mot_de_passe="x", role="employe")
        db.add(u)
        db.flush()
        champs = dict(
            utilisateur_id=u.id, matricule=f"ARRA-X{u.id:03d}",
            poste="Ingénieur Logiciel", departement="Informatique",
            salaire_base=9500, tjm=None, date_embauche=date(2026, 9, 1),
            type_contrat=contrat, entite="MA", sexe="M",
            date_naissance=date(1994, 3, 15), lieu_naissance="Casablanca",
            nationalite="Marocaine", cin="BK284571",
            adresse="12 rue des Lilas, Casablanca", telephone="+212 6",
        )
        champs.update(kw)
        e = Employe(**champs)
        db.add(e)
        db.commit()
        return e

    print("\n— 1. Chaque type de contrat a son modèle —")
    for contrat, attendu in (("CDI", "contrat_cdi"), ("CDD", "contrat_cdd"),
                             ("Freelance", "contrat_auto_entrepreneur"),
                             ("Prestataire", "contrat_prestation")):
        verifier(modele_pour(contrat) == attendu, f"{contrat} → {attendu}", str(modele_pour(contrat)))
    verifier(modele_pour("Stage") is None, "Stage → aucun modèle (assumé)")
    verifier(modele_pour(None) is None, "type absent → aucun modèle")

    print("\n— 2. Les quatre contrats produisent un PDF —")
    societe = dict(
        presta_societe="I-ETERIA", presta_forme="SARL", presta_capital="300.000 MAD",
        presta_rc="115899", presta_siege="2 Place Abou Baker Essadiq, Rabat",
        presta_gerant="M. BEGDOURI ACHKARI Mohammed Amine",
    )
    salaries = {
        "CDI": creer("alpha", "CDI"),
        "CDD": creer("bravo", "CDD"),
        "Freelance": creer("charlie", "Freelance", salaire_base=0, tjm=650),
        "Prestataire": creer("delta", "Prestataire", salaire_base=0, tjm=3200, **societe),
    }
    for contrat, emp in salaries.items():
        try:
            pdf, nom_fichier = generer_pdf_contrat(db, emp, utilisateur_id=None)
            db.commit()
            verifier(pdf[:4] == b"%PDF" and len(pdf) > 5000,
                     f"{contrat} : PDF de {len(pdf) // 1024} Ko", f"{len(pdf)} octets")
            verifier(nom_fichier.endswith(".pdf") and contrat in nom_fichier,
                     f"{contrat} : nom de fichier explicite", nom_fichier)
        except ContratIndisponible as e:
            verifier(False, f"{contrat} : génération", str(e))

    print("\n— 3. Numérotation : compteur annuel PARTAGÉ —")
    numeros = [
        db.query(Contrat).filter_by(employe_id=e.id).first().numero
        for e in salaries.values()
    ]
    annee = date.today().year
    verifier(numeros == [f"{i:04d}-{annee}" for i in range(1, 5)],
             "0001 à 0004 sur la même suite, tous types confondus", str(numeros))
    verifier(len(set(numeros)) == 4, "aucun doublon")

    print("\n— 4. Le numéro est attribué UNE SEULE FOIS —")
    emp = salaries["CDI"]
    premier = db.query(Contrat).filter_by(employe_id=emp.id).first().numero
    generer_pdf_contrat(db, emp, utilisateur_id=None)
    db.commit()
    apres = db.query(Contrat).filter_by(employe_id=emp.id).all()
    verifier(len(apres) == 1, "réimprimer ne crée pas un second contrat", str(len(apres)))
    verifier(apres[0].numero == premier, "le numéro ne change pas", apres[0].numero)

    print("\n— 5. Changer de type est un contrat distinct —")
    emp.type_contrat = "CDD"
    db.commit()
    generer_pdf_contrat(db, emp, utilisateur_id=None)
    db.commit()
    tous = db.query(Contrat).filter_by(employe_id=emp.id).all()
    verifier(len(tous) == 2, "un CDI puis un CDD → deux numéros", str(len(tous)))

    print("\n— 6. Une fiche incomplète est refusée, en nommant les manques —")
    sans_lieu = creer("echo", "CDI", lieu_naissance=None)
    try:
        generer_pdf_contrat(db, sans_lieu)
        verifier(False, "lieu de naissance manquant → refus")
    except ContratIndisponible as e:
        verifier("Lieu de naissance" in str(e), "le refus nomme le champ manquant", str(e))

    presta_nu = creer("foxtrot", "Prestataire", salaire_base=0, tjm=3200)
    try:
        generer_pdf_contrat(db, presta_nu)
        verifier(False, "société prestataire manquante → refus")
    except ContratIndisponible as e:
        verifier("Société prestataire" in str(e),
                 "le refus cite les mentions légales manquantes", str(e))

    stagiaire = creer("golf", "Stage")
    try:
        generer_pdf_contrat(db, stagiaire)
        verifier(False, "stagiaire → refus explicite")
    except ContratIndisponible as e:
        verifier("Stage" in str(e) or "modèle" in str(e),
                 "le refus explique qu'aucun modèle n'existe", str(e))

    print("\n— 7. Aucun numéro n'est consommé par un refus —")
    verifier(db.query(Contrat).count() == 5,
             "5 contrats en registre, les refus n'en ont créé aucun",
             str(db.query(Contrat).count()))

    print("\n— 8. Le contenu reprend bien les données du salarié —")
    from jinja2 import Environment
    from app.services.contrats import construire_donnees
    emp_presta = salaries["Prestataire"]
    contrat_presta = db.query(Contrat).filter_by(employe_id=emp_presta.id).first()
    donnees = construire_donnees(db, emp_presta, contrat_presta.numero)
    modele = db.query(TemplateModel).filter_by(type="contrat_prestation").first()
    html = Environment().from_string(modele.contenu_html).render(**donnees)
    for attendu in ("I-ETERIA", "115899", "BEGDOURI", "3 200,00 MAD", contrat_presta.numero):
        verifier(attendu in html, f"le contrat porte « {attendu} »")
    verifier("{{" not in html, "aucune variable Jinja non substituée")

    db.close()

    print()
    if ECHECS:
        print(f"RESULTAT : {len(ECHECS)} ECHEC(S) — " + " · ".join(ECHECS))
        sys.exit(1)
    print("RESULTAT : CONTRATS OK")


if __name__ == "__main__":
    main()
