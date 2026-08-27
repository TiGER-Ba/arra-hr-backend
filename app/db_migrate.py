"""Migrations légères idempotentes (create_all n'altère pas les tables existantes).

Doit tourner AVANT toute requête ORM sur une table modifiée — donc appelé à la
fois par `seed.py` (au démarrage du conteneur, avant uvicorn) et par le lifespan
de l'app. Compatible PostgreSQL et SQLite, sans Alembic.

⚠️ Les NOUVELLES tables (societes, projets…) sont créées automatiquement par
`Base.metadata.create_all` : seules les COLONNES ajoutées à une table existante
nécessitent un ALTER ici.

RÈGLE : chaque bloc de migration est INDÉPENDANT. Une table absente ou une
erreur ponctuelle ne doit jamais empêcher les migrations suivantes de s'exécuter.
"""
from sqlalchemy import inspect, text

from app.database import engine


def _colonnes(table: str) -> set[str] | None:
    """Colonnes existantes, ou None si la table n'existe pas encore."""
    try:
        return {c["name"] for c in inspect(engine).get_columns(table)}
    except Exception:
        return None


def _add_column(cols: set[str], table: str, name: str, ddl_type: str) -> None:
    if name in cols:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}"))
        cols.add(name)
        print(f"[migrate] {table}.{name} ajouté")
    except Exception as e:
        print(f"[migrate] ERREUR ajout {table}.{name} : {e}")


def _executer(sql: str, description: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(sql))
    except Exception as e:
        print(f"[migrate] {description} ignoré ({e})")


def run_migrations() -> None:
    """Non-fatal : toute erreur est loggée mais n'interrompt PAS le démarrage."""
    _migrer_utilisateurs()
    _migrer_templates()
    _migrer_pointage()
    _migrer_feuilles_temps()


def _migrer_utilisateurs() -> None:
    cols = _colonnes("utilisateurs")
    if cols is None:
        return
    _add_column(cols, "utilisateurs", "prenom", "VARCHAR(100)")
    _add_column(cols, "utilisateurs", "invite_token", "VARCHAR(128)")
    _add_column(cols, "utilisateurs", "invite_token_expire", "TIMESTAMP")


def _migrer_templates() -> None:
    # `personnalise` protège un template retouché à la main contre la
    # resynchronisation automatique effectuée par seed.py.
    cols = _colonnes("templates")
    if cols is None:
        return
    _add_column(cols, "templates", "personnalise", "BOOLEAN DEFAULT FALSE NOT NULL")


def _migrer_pointage() -> None:
    """Pointage v2 : plusieurs lignes par jour, rattachées à un projet.

    ⚠️ Les données existantes sont PRÉSERVÉES : les anciennes entrées ne
    contenaient que des absences d'une journée entière, on les qualifie donc en
    categorie='absence' / valeur=1.
    """
    cols = _colonnes("pointages")
    if cols is None:
        return

    premiere_fois = "categorie" not in cols
    _add_column(cols, "pointages", "categorie", "VARCHAR(20)")
    _add_column(cols, "pointages", "projet_id", "INTEGER")
    _add_column(cols, "pointages", "valeur", "NUMERIC(3,2)")
    # Travail hors jours ouvrés (samedi, dimanche, férié) → majoration en paie
    _add_column(cols, "pointages", "exceptionnel", "BOOLEAN DEFAULT FALSE NOT NULL")

    # Reprise des lignes historiques (toutes étaient des absences d'une journée)
    if premiere_fois:
        _executer("UPDATE pointages SET categorie = 'absence' WHERE categorie IS NULL",
                  "qualification des pointages historiques")
        _executer("UPDATE pointages SET valeur = 1 WHERE valeur IS NULL",
                  "valeur par défaut des pointages historiques")
        print("[migrate] pointages historiques qualifiés en 'absence' (valeur 1)")

    # La contrainte « une seule ligne par jour » empêche de répartir une journée
    # entre deux projets (0,5 + 0,5) : on la retire.
    _retirer_contrainte_unique_jour()


def _retirer_contrainte_unique_jour() -> None:
    """Retire la contrainte UNIQUE(employe_id, date_jour) de `pointages`.

    PostgreSQL (production) sait le faire directement. SQLite (développement et
    tests) ne le peut pas : la seule voie est de reconstruire la table, ce qui
    est fait ici dans UNE transaction — en cas d'échec, rien n'est modifié.
    """
    dialecte = engine.dialect.name

    if dialecte != "sqlite":
        for nom in ("uq_pointage_employe_jour", "pointages_employe_id_date_jour_key"):
            _executer(f"ALTER TABLE pointages DROP CONSTRAINT IF EXISTS {nom}",
                      f"retrait de la contrainte {nom}")
        return

    # ── Chemin SQLite : reconstruction ──
    try:
        with engine.connect() as conn:
            ddl = conn.execute(text(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='pointages'"
            )).scalar() or ""
        if "UNIQUE" not in ddl.upper():
            return  # contrainte déjà absente

        with engine.begin() as conn:
            infos = conn.execute(text("PRAGMA table_info(pointages)")).fetchall()
            # ⚠️ Les colonnes sont reprises DYNAMIQUEMENT : une liste écrite en
            # dur se désynchroniserait au prochain ajout de colonne.
            definitions, noms = [], []
            for info in infos:
                nom, type_sql, pk = info[1], info[2] or "TEXT", info[5]
                noms.append(nom)
                definitions.append(f"{nom} {type_sql}" + (" PRIMARY KEY" if pk else ""))
            liste = ", ".join(noms)
            conn.execute(text(f"CREATE TABLE pointages_migration ({', '.join(definitions)})"))
            conn.execute(text(
                f"INSERT INTO pointages_migration ({liste}) SELECT {liste} FROM pointages"
            ))
            conn.execute(text("DROP TABLE pointages"))
            conn.execute(text("ALTER TABLE pointages_migration RENAME TO pointages"))
        print("[migrate] contrainte d'unicité par jour retirée (reconstruction SQLite)")
    except Exception as e:
        print(f"[migrate] ERREUR retrait de la contrainte d'unicité : {e}")


def _migrer_feuilles_temps() -> None:
    cols = _colonnes("feuilles_temps")
    if cols is None:
        return
    _add_column(cols, "feuilles_temps", "valide_par_id", "INTEGER")
    _add_column(cols, "feuilles_temps", "valide_le", "TIMESTAMP")
    _add_column(cols, "feuilles_temps", "motif_rejet", "TEXT")
    _add_column(cols, "feuilles_temps", "commentaire", "TEXT")
