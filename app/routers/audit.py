"""Journal d'audit — consultation (admin uniquement)."""
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.audit import JournalAudit
from app.models.user import Utilisateur
from app.services.auth import require_admin

router = APIRouter()


@router.get("")
def liste_audit(
    action: Optional[str] = None,
    limit: int = 100,
    current_user: Utilisateur = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(JournalAudit)
    if action:
        q = q.filter(JournalAudit.action == action)
    rows = q.order_by(JournalAudit.created_at.desc()).limit(min(max(limit, 1), 500)).all()
    return [
        {
            "id": r.id,
            "acteur_nom": r.acteur_nom,
            "acteur_role": r.acteur_role,
            "action": r.action,
            "cible_type": r.cible_type,
            "cible_id": r.cible_id,
            "cible_libelle": r.cible_libelle,
            "details": r.details,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
