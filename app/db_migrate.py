"""Migrations légères idempotentes (create_all n'altère pas les tables existantes).

Doit tourner AVANT toute requête ORM sur une table modifiée — donc appelé à la
fois par `seed.py` (au démarrage du conteneur, avant uvicorn) et par le lifespan
de l'app. Compatible PostgreSQL et SQLite, sans Alembic.
"""
from sqlalchemy import inspect, text

from app.database import engine


def run_migrations():
    """Non-fatal : toute erreur est loggée mais n'interrompt PAS le démarrage."""
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("utilisateurs")}
    except Exception as e:
        print(f"[migrate] inspection ignorée ({e})")
        return  # table pas encore créée : create_all s'en charge avec le bon schéma
    if "prenom" not in cols:
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE utilisateurs ADD COLUMN prenom VARCHAR(100)"))
            print("[migrate] utilisateurs.prenom ajouté")
        except Exception as e:
            print(f"[migrate] ERREUR ajout prenom : {e}")
