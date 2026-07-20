"""Migrations légères idempotentes (create_all n'altère pas les tables existantes).

Doit tourner AVANT toute requête ORM sur une table modifiée — donc appelé à la
fois par `seed.py` (au démarrage du conteneur, avant uvicorn) et par le lifespan
de l'app. Compatible PostgreSQL et SQLite, sans Alembic.

⚠️ Les NOUVELLES tables (ex. journal_audit) sont créées automatiquement par
`Base.metadata.create_all` : seules les COLONNES ajoutées à une table existante
nécessitent un ALTER ici.
"""
from sqlalchemy import inspect, text

from app.database import engine


def _add_column(cols: set[str], table: str, name: str, ddl_type: str) -> None:
    if name in cols:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
        print(f"[migrate] {table}.{name} ajouté")
    except Exception as e:
        print(f"[migrate] ERREUR ajout {table}.{name} : {e}")


def run_migrations():
    """Non-fatal : toute erreur est loggée mais n'interrompt PAS le démarrage."""
    try:
        insp = inspect(engine)
        cols = {c["name"] for c in insp.get_columns("utilisateurs")}
    except Exception as e:
        print(f"[migrate] inspection ignorée ({e})")
        return  # table pas encore créée : create_all s'en charge avec le bon schéma

    _add_column(cols, "utilisateurs", "prenom", "VARCHAR(100)")
    _add_column(cols, "utilisateurs", "invite_token", "VARCHAR(128)")
    _add_column(cols, "utilisateurs", "invite_token_expire", "TIMESTAMP")
