"""Helper de journalisation d'audit.

`log_action` ajoute une entrée à la session courante SANS committer : c'est
l'endpoint appelant qui fait `db.commit()` (l'entrée d'audit part donc dans la
même transaction que l'action tracée). Toute erreur est avalée : l'audit ne doit
jamais faire échouer l'opération métier.
"""
from sqlalchemy.orm import Session

from app.models.audit import JournalAudit
from app.models.user import Utilisateur


def _nom_complet(u: Utilisateur | None) -> str | None:
    if not u:
        return None
    prenom = (u.prenom + " ") if getattr(u, "prenom", None) else ""
    return f"{prenom}{u.nom}".strip() or None


def log_action(
    db: Session,
    acteur: Utilisateur | None,
    action: str,
    *,
    cible_type: str | None = None,
    cible_id: int | None = None,
    cible_libelle: str | None = None,
    details: str | None = None,
) -> None:
    try:
        db.add(JournalAudit(
            acteur_id=acteur.id if acteur else None,
            acteur_nom=_nom_complet(acteur),
            acteur_role=acteur.role if acteur else None,
            action=action,
            cible_type=cible_type,
            cible_id=cible_id,
            cible_libelle=cible_libelle,
            details=details,
        ))
    except Exception as e:  # noqa: BLE001 — l'audit ne bloque jamais l'action
        print(f"[audit] échec journalisation ({action}) : {e}")
