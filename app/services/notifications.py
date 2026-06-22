from sqlalchemy.orm import Session

from app.models.notification import Notification


def notifier(
    db: Session,
    utilisateur_id: int,
    type: str,
    titre: str,
    message: str,
    lien: str | None = None,
) -> Notification:
    """Helper pour créer une notification pour un utilisateur."""
    notif = Notification(
        utilisateur_id=utilisateur_id,
        type=type,
        titre=titre,
        message=message,
        lien=lien,
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    return notif


def notifier_rh(db: Session, type: str, titre: str, message: str, lien: str | None = None):
    """Notifier tous les RH/admin de l'organisation."""
    from app.models.user import Utilisateur
    rhs = db.query(Utilisateur).filter(Utilisateur.role.in_(["rh", "admin"]), Utilisateur.is_active == True).all()
    for rh in rhs:
        db.add(Notification(
            utilisateur_id=rh.id,
            type=type,
            titre=titre,
            message=message,
            lien=lien,
        ))
    db.commit()
