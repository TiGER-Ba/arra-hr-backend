"""Vérifie la génération du CRA (feuille des temps mensuelle) sans base de données."""
import sys
import types
from datetime import date, datetime

import app.services.cra as cra

ok = True

# ── Jeux de données simulés ─────────────────────────────────────────────────
utilisateur = types.SimpleNamespace(nom="CHALOUACH", prenom="Abdelkarim")
employe = types.SimpleNamespace(
    id=1, matricule="EMP007", poste="Ingénieur", departement="Informatique",
    utilisateur=utilisateur,
)


class Pointage:
    """Reflète le modèle v2 : catégorie, projet éventuel et valeur (0,5 ou 1)."""

    def __init__(self, jour, type_, categorie="absence", projet_id=None, valeur=1.0):
        self.date_jour = date(2026, 7, jour)
        self.type = type_
        self.categorie = categorie
        self.projet_id = projet_id
        self.valeur = valeur
        self.employe_id = 1


class FakeQuery:
    def __init__(self, resultat): self.resultat = resultat
    def filter(self, *a, **k): return self
    def filter_by(self, **k): return self
    def all(self): return self.resultat if isinstance(self.resultat, list) else []
    def first(self): return None if isinstance(self.resultat, list) else self.resultat


class FakeDB:
    def __init__(self, pointages): self.pointages = pointages
    def query(self, modele):
        nom = getattr(modele, "__name__", str(modele))
        if "Pointage" in nom:
            return FakeQuery(self.pointages)
        return FakeQuery([])
    def add(self, *a): pass
    def commit(self): pass


# Juillet 2026 : 3 j de congés payés, et une journée partagée entre 2 projets
pointages = [
    Pointage(6, "conge_paye"), Pointage(7, "conge_paye"), Pointage(8, "conge_paye"),
    Pointage(20, "normale", categorie="production", projet_id=1, valeur=0.5),
    Pointage(20, "normale", categorie="production", projet_id=2, valeur=0.5),
]
cra.feries_du_mois = lambda db, a, m: {}
import app.services.feries as feries_mod
feries_mod.feries_du_mois = lambda db, a, m: {}

donnees = cra.construire_donnees_cra(FakeDB(pointages), employe, 2026, 7)

print("— Données du CRA (juillet 2026) —")
print(f"  salarié          : {donnees['prenom_nom']} ({donnees['matricule']})")
print(f"  jours du mois    : {donnees['nb_jours']}")
print(f"  jours ouvrés     : {donnees['jours_ouvres']}")
print(f"  production       : {donnees['production']} j")
print(f"  absences         : {donnees['total_absence']} j")
print(f"  lignes grille    : {[l['libelle'] for l in donnees['lignes']]}")

# Juillet 2026 : 31 jours, 23 jours ouvrés
if donnees["nb_jours"] != 31:
    ok = False; print("  ECHEC nb_jours")
if donnees["jours_ouvres"] != 23:
    ok = False; print(f"  ECHEC jours_ouvres = {donnees['jours_ouvres']} (attendu 23)")
if float(donnees["production"]) + float(donnees["total_absence"]) != donnees["jours_ouvres"]:
    ok = False; print("  ECHEC production + absences != jours ouvrés")
if float(donnees["total_absence"]) != 3:
    ok = False; print(f"  ECHEC absences = {donnees['total_absence']} (attendu 3)")
# La journée du 20 est partagée 0,5 + 0,5 : elle doit compter pour 1 jour au total
if len([l for l in donnees["lignes"] if l["categorie"] == "production"]) < 2:
    ok = False; print("  ECHEC les deux projets du 20 ne forment pas deux lignes")

# ── Rendu HTML ──────────────────────────────────────────────────────────────
print("\n— Rendu HTML —")
html = cra.rendre_html_cra(FakeDB(pointages), employe, 2026, 7)
for attendu in ["Feuille des temps", "CHALOUACH", "EMP007", "Nb jours ouvrés : 23",
                "Congé payé", "Validation 1", "Validation 2"]:
    if attendu in html:
        print(f"  OK    « {attendu} » présent")
    else:
        ok = False; print(f"  ECHEC « {attendu} » absent")

if "display: flex" in html or "display:flex" in html:
    ok = False; print("  ECHEC display:flex présent (mal rendu par WeasyPrint)")
else:
    print("  OK    aucun display:flex")

# ── Génération PDF ──────────────────────────────────────────────────────────
print("\n— Génération PDF —")
try:
    pdf = cra.generer_pdf_cra(FakeDB(pointages), employe, 2026, 7)
    if pdf[:4] == b"%PDF" and len(pdf) > 3000:
        print(f"  OK    PDF valide ({len(pdf)//1024} Ko)")
        open("cra_apercu.pdf", "wb").write(pdf)
        print("  → écrit dans backend/cra_apercu.pdf")
    else:
        ok = False; print(f"  ECHEC PDF douteux ({len(pdf)} octets)")
except RuntimeError as e:
    print(f"  IGNORÉ (WeasyPrint indisponible en local) : {str(e)[:90]}")

print(f"\n  nom de fichier : {cra.nom_fichier_cra(employe, 2026, 7)}")
print("\nRESULTAT :", "CRA OK" if ok else "DES CONTROLES ONT ECHOUE")
sys.exit(0 if ok else 1)
