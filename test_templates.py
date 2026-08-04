"""Vérifie que les 10 templates PDF se rendent sans erreur et placent
correctement signature + cachet (pas de flex, blocs non coupés entre pages).
"""
import pathlib
import re

from jinja2 import Environment

DOSSIER = pathlib.Path(__file__).parent / "app" / "templates"

# Jeu de données représentatif (images en data URI, comme en production)
PIXEL = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAA"
         "C0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
DONNEES = {
    "nom_employe": "Karim Benjelloun", "email_employe": "k.b@arra-engineering.com",
    "matricule": "EMP001", "poste": "Ingénieur", "departement": "Informatique",
    "salaire_base": 12000.0, "date_embauche": "01/01/2024", "statut_employe": "actif",
    "type_contrat": "CDI", "cin": "WA123456", "cnss": "123456789",
    "adresse": "Casablanca", "telephone": "+212600000000",
    "date_generation": "29/07/2026", "lieu_signature": "Casablanca",
    "signataire_nom": "El Mahdi HMOUCH", "signataire_fonction": "Directeur ARRA ENGINEERING Maroc",
    "signature_url": PIXEL, "cachet_url": PIXEL,
    "mois": "07", "annee": "2026", "date_debut": "2026-08-01", "date_fin": "2026-08-15",
    "type_conge": "annuel", "motif": "Congé annuel", "nombre_jours": 10,
    "destination": "Rabat", "date_depart": "2026-08-01", "date_retour": "2026-08-03",
    "montant": 5000, "nom_formation": "Python", "organisme": "Centre X",
    "poste_souhaite": "Chef de projet", "departement_cible": "Direction",
}

ok = True
env = Environment()

print("— Rendu des templates —")
for fichier in sorted(DOSSIER.glob("*.html")):
    contenu = fichier.read_text(encoding="utf-8")

    # 1) le template doit se rendre sans erreur
    try:
        html = env.from_string(contenu).render(**DONNEES)
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"  ECHEC rendu {fichier.name} : {e}")
        continue

    # 2) plus de flexbox dans les blocs de signature (mal supporté par WeasyPrint)
    if re.search(r"\.signature[^{]*\{[^}]*display:\s*flex", contenu):
        ok = False
        print(f"  ECHEC {fichier.name} : display:flex encore présent dans .signature")
        continue

    # 3) la signature ne doit pas pouvoir être coupée entre deux pages
    if "page-break-inside" not in contenu:
        ok = False
        print(f"  ECHEC {fichier.name} : page-break-inside manquant")
        continue

    # 4) signature ET cachet doivent apparaître dans le HTML produit
    if html.count(PIXEL) < 2:
        ok = False
        print(f"  ECHEC {fichier.name} : signature et/ou cachet absents du rendu")
        continue

    print(f"  OK    {fichier.name}")

print("\nRESULTAT :", "TEMPLATES OK" if ok else "DES TEMPLATES ONT ECHOUE")
raise SystemExit(0 if ok else 1)
