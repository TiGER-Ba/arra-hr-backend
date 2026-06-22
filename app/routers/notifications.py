from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.notification import Notification
from app.models.user import Utilisateur
from app.schemas.notification import NotificationOut
from app.services.auth import get_current_user

router = APIRouter()


@router.get("/", response_model=list[NotificationOut])
def liste_notifications(
    non_lues: bool = False,
    limite: int = 50,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).filter(Notification.utilisateur_id == current_user.id)
    if non_lues:
        q = q.filter(Notification.lu == False)
    return q.order_by(Notification.created_at.desc()).limit(limite).all()


@router.get("/non-lues/count")
def count_non_lues(
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = db.query(Notification).filter(
        Notification.utilisateur_id == current_user.id,
        Notification.lu == False,
    ).count()
    return {"count": count}


@router.post("/{notif_id}/marquer-lue")
def marquer_lue(
    notif_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.utilisateur_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification introuvable")
    notif.lu = True
    db.commit()
    return {"message": "Marquée comme lue"}


@router.post("/marquer-toutes-lues")
def marquer_toutes_lues(
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(Notification).filter(
        Notification.utilisateur_id == current_user.id,
        Notification.lu == False,
    ).update({Notification.lu: True})
    db.commit()
    return {"message": "Toutes marquées comme lues"}


@router.delete("/{notif_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_notif(
    notif_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = db.query(Notification).filter(
        Notification.id == notif_id,
        Notification.utilisateur_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification introuvable")
    db.delete(notif)
    db.commit()
