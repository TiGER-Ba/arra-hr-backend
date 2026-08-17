"""Vérifie que la migration du pointage v1 → v2 PRÉSERVE les données existantes.

Simule une base à l'ancien format (une seule ligne par jour, absences
uniquement), applique la migration, puis contrôle que rien n'est perdu et que
la nouvelle capacité (plusieurs lignes par jour) est bien débloquée.
"""
import os
import sys
import tempfile

BASE = os.path.join(tempfile.gettempdir(), "test_migration_pointage.db")
if os.path.exists(BASE):
    os.remove(BASE)
os.environ["DATABASE_URL"] = f"sqlite:///{BASE}"

from sqlalchemy import create_engine, text  # noqa: E402

moteur = create_engine(f"sqlite:///{BASE}")

print("— 1. Création d'une base à l'ANCIEN format —")
with moteur.begin() as c:
    c.execute(text("""
        CREATE TABLE pointages (
            id INTEGER PRIMARY KEY,
            employe_id INTEGER NOT NULL,
            date_jour DATE NOT NULL,
            type VARCHAR(30) NOT NULL,
            commentaire VARCHAR(255),
            CONSTRAINT uq_pointage_employe_jour UNIQUE (employe_id, date_jour)
        )
    """))
    c.execute(text("""
        CREATE TABLE feuilles_temps (
            id INTEGER PRIMARY KEY, employe_id INTEGER, annee INTEGER,
            mois INTEGER, statut VARCHAR(20), updated_at TIMESTAMP
        )
    """))
    for j, t in [(6, "conge_paye"), (7, "conge_paye"), (14, "maladie")]:
        c.execute(text(
            "INSERT INTO pointages (employe_id, date_jour, type) VALUES (1, :d, :t)"
        ), {"d": f"2026-07-{j:02d}", "t": t})
    c.execute(text(
        "INSERT INTO feuilles_temps (employe_id, annee, mois, statut) VALUES (1, 2026, 7, 'soumise')"
    ))

with moteur.connect() as c:
    avant = c.execute(text("SELECT COUNT(*) FROM pointages")).scalar()
print(f"  {avant} pointages historiques insérés")

print("\n— 2. Application de la migration —")
import app.database as dbmod  # noqa: E402
dbmod.engine = moteur
import app.db_migrate as mig  # noqa: E402
mig.engine = moteur
mig.run_migrations()

print("\n— 3. Contrôles —")
ok = True
with moteur.connect() as c:
    apres = c.execute(text("SELECT COUNT(*) FROM pointages")).scalar()
    if apres == avant:
        print(f"  OK    aucune perte de donnée ({apres} lignes)")
    else:
        ok = False
        print(f"  ECHEC {avant} lignes avant, {apres} après")

    lignes = c.execute(text("SELECT type, categorie, valeur FROM pointages ORDER BY id")).fetchall()
    if all(l[1] == "absence" and float(l[2]) == 1.0 for l in lignes):
        print("  OK    historique qualifié en categorie='absence', valeur=1")
    else:
        ok = False
        print(f"  ECHEC qualification incorrecte : {lignes}")

    if all(l[0] in ("conge_paye", "maladie") for l in lignes):
        print("  OK    les types d'origine sont conservés")
    else:
        ok = False
        print(f"  ECHEC types altérés : {lignes}")

    cols = {r[1] for r in c.execute(text("PRAGMA table_info(feuilles_temps)")).fetchall()}
    if {"valide_par_id", "valide_le", "motif_rejet"} <= cols:
        print("  OK    colonnes de validation ajoutées")
    else:
        ok = False
        print(f"  ECHEC colonnes manquantes : {cols}")

print("\n— 4. Nouvelle capacité : deux demi-journées le même jour —")
try:
    with moteur.begin() as c:
        c.execute(text(
            "INSERT INTO pointages (employe_id, date_jour, type, categorie, valeur, projet_id)"
            " VALUES (1, '2026-07-20', 'normale', 'production', 0.5, 1)"
        ))
        c.execute(text(
            "INSERT INTO pointages (employe_id, date_jour, type, categorie, valeur, projet_id)"
            " VALUES (1, '2026-07-20', 'normale', 'production', 0.5, 2)"
        ))
    print("  OK    2 lignes acceptées le même jour (contrainte bien retirée)")
except Exception as e:
    ok = False
    print(f"  ECHEC la contrainte bloque encore : {e}")

print("\nRESULTAT :", "MIGRATION SANS PERTE" if ok else "DES CONTROLES ONT ECHOUE")
sys.exit(0 if ok else 1)
