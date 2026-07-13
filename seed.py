"""
Script de seed — crée les comptes initiaux et les templates de documents.
Lancer depuis backend/ avec le venv activé :
    python seed.py
"""
import sys
import os
from datetime import date

sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal, Base, engine
import app.models  # noqa — enregistre tous les modèles
from app.models.user import Utilisateur
from app.models.employee import Employe
from app.models.rh import RH
from app.models.template import Template
from app.services.auth import get_password_hash

Base.metadata.create_all(bind=engine)

TEMPLATES = [
    {
        "type": "bulletin_paie",
        "nom": "Bulletin de Paie",
        "champs_requis": ["mois", "annee"],
        "contenu_html": open("app/templates/bulletin_paie.html", encoding="utf-8").read(),
    },
    {
        "type": "attestation_travail",
        "nom": "Attestation de Travail",
        "champs_requis": [],
        "contenu_html": open("app/templates/attestation_travail.html", encoding="utf-8").read(),
    },
    {
        "type": "attestation_salaire",
        "nom": "Attestation de Salaire",
        "champs_requis": [],
        "contenu_html": open("app/templates/attestation_salaire.html", encoding="utf-8").read(),
    },
    {
        "type": "attestation_conge",
        "nom": "Attestation de Congé",
        "champs_requis": ["date_debut", "date_fin"],
        "contenu_html": open("app/templates/attestation_conge.html", encoding="utf-8").read(),
    },
    {
        "type": "demande_conge",
        "nom": "Demande de Congé",
        "champs_requis": ["type_conge", "date_debut", "date_fin", "motif"],
        "contenu_html": open("app/templates/demande_conge.html", encoding="utf-8").read(),
    },
    {
        "type": "ordre_mission",
        "nom": "Ordre de Mission",
        "champs_requis": ["destination", "date_depart", "date_retour", "motif"],
        "contenu_html": open("app/templates/ordre_mission.html", encoding="utf-8").read(),
    },
    {
        "type": "demande_avance_salaire",
        "nom": "Demande d'Avance sur Salaire",
        "champs_requis": ["montant", "motif"],
        "contenu_html": open("app/templates/demande_avance_salaire.html", encoding="utf-8").read(),
    },
    {
        "type": "certificat_presence",
        "nom": "Certificat de Présence",
        "champs_requis": [],
        "contenu_html": open("app/templates/certificat_presence.html", encoding="utf-8").read(),
    },
    {
        "type": "demande_formation",
        "nom": "Demande de Formation",
        "champs_requis": ["nom_formation", "organisme", "date_debut", "date_fin"],
        "contenu_html": open("app/templates/demande_formation.html", encoding="utf-8").read(),
    },
    {
        "type": "demande_mutation",
        "nom": "Demande de Mutation",
        "champs_requis": ["poste_souhaite", "departement_cible", "motif"],
        "contenu_html": open("app/templates/demande_mutation.html", encoding="utf-8").read(),
    },
]

USERS = [
    {
        "user": {"nom": "Administrateur", "email": "admin@arra.ma", "mot_de_passe": "admin123456", "role": "admin"},
        "extra": {},
    },
    {
        "user": {"nom": "Admin RH", "email": "rh@arra.ma", "mot_de_passe": "rh123456", "role": "rh"},
        "extra": {"service": "Direction des Ressources Humaines"},
    },
    {
        "user": {"nom": "Karim Benjelloun", "email": "karim@arra.ma", "mot_de_passe": "emp123456", "role": "employe"},
        "extra": {
            "matricule": "EMP001",
            "poste": "Ingénieur Logiciel",
            "departement": "Informatique",
            "salaire_base": 9500.00,
            "date_embauche": date(2022, 3, 15),
            "type_contrat": "CDI",
            "cin": "BK284571",
            "cnss": "154028391",
            "adresse": "Résidence Al Massira, Bd Anfa, Casablanca",
            "telephone": "+212 661 23 45 67",
        },
    },
    {
        "user": {"nom": "Sara El Amrani", "email": "sara@arra.ma", "mot_de_passe": "emp123456", "role": "employe"},
        "extra": {
            "matricule": "EMP002",
            "poste": "Chef de Projet",
            "departement": "Gestion de Projet",
            "salaire_base": 11000.00,
            "date_embauche": date(2021, 6, 1),
            "type_contrat": "CDI",
            "cin": "WA315892",
            "cnss": "154167203",
            "adresse": "Lotissement Riad, Avenue Mohammed V, Rabat",
            "telephone": "+212 662 88 12 04",
        },
    },
    {
        "user": {"nom": "Ahmed Tahiri", "email": "ahmed@arra.ma", "mot_de_passe": "emp123456", "role": "employe"},
        "extra": {
            "matricule": "EMP003",
            "poste": "Analyste Financier",
            "departement": "Finance",
            "salaire_base": 8500.00,
            "date_embauche": date(2023, 1, 10),
            "type_contrat": "CDD",
            "cin": "AB429077",
            "cnss": "154284619",
            "adresse": "Hay Hassani, Rue 12 Nr 8, Casablanca",
            "telephone": "+212 663 47 90 21",
        },
    },
]


def seed():
    db = SessionLocal()
    try:
        # Templates
        existing_templates = {t.type for t in db.query(Template).all()}
        for tpl in TEMPLATES:
            if tpl["type"] not in existing_templates:
                db.add(Template(
                    type=tpl["type"],
                    nom=tpl["nom"],
                    contenu_html=tpl["contenu_html"],
                    champs_requis=tpl["champs_requis"],
                    actif=True,
                ))
                print(f"  [template] {tpl['nom']}")
        db.commit()

        # Users
        for entry in USERS:
            u_data = entry["user"]
            if db.query(Utilisateur).filter(Utilisateur.email == u_data["email"]).first():
                print(f"  [skip] {u_data['email']} déjà existant")
                continue

            user = Utilisateur(
                nom=u_data["nom"],
                email=u_data["email"],
                mot_de_passe=get_password_hash(u_data["mot_de_passe"]),
                role=u_data["role"],
            )
            db.add(user)
            db.flush()

            if u_data["role"] == "rh":
                db.add(RH(utilisateur_id=user.id, service=entry["extra"]["service"]))
            elif u_data["role"] == "employe":
                ex = entry["extra"]
                db.add(Employe(
                    utilisateur_id=user.id,
                    matricule=ex["matricule"],
                    poste=ex["poste"],
                    departement=ex["departement"],
                    salaire_base=ex["salaire_base"],
                    date_embauche=ex["date_embauche"],
                    type_contrat=ex.get("type_contrat", "CDI"),
                    cin=ex.get("cin"),
                    cnss=ex.get("cnss"),
                    adresse=ex.get("adresse"),
                    telephone=ex.get("telephone"),
                ))
            db.commit()
            print(f"  [user] {u_data['email']} ({u_data['role']})")

        print("\nSeed terminé avec succès!")
        print("\nComptes créés :")
        print("  Admin   : admin@arra.ma    / admin123456")
        print("  RH      : rh@arra.ma       / rh123456")
        print("  Employé : karim@arra.ma    / emp123456")
        print("  Employé : sara@arra.ma     / emp123456")
        print("  Employé : ahmed@arra.ma    / emp123456")

    finally:
        db.close()


if __name__ == "__main__":
    print("Initialisation de la base de données...")
    seed()
