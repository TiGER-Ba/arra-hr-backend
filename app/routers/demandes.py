from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.demande import Demande
from app.models.employee import Employe
from app.models.user import Utilisateur
from app.schemas.demande import DemandeOut
from app.services.auth import get_current_user
from app.services.demande_service import DEMANDES_CONFIG, champs_meta, creer_demande

router = APIRouter()


def _get_employe(current_user: Utilisateur, db: Session) -> Employe:
    employe = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
    if not employe:
        raise HTTPException(status_code=404, detail="Profil employé introuvable")
    return employe


class DemandeCreate(BaseModel):
    type: str
    donnees: dict = {}


@router.get("/types")
def types_demandes(current_user: Utilisateur = Depends(get_current_user)):
    """Config des types de documents demandables (pour le formulaire dynamique)."""
    return [
        {"key": k, "label": v["label"], "champs": champs_meta(k)}
        for k, v in DEMANDES_CONFIG.items()
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def creer_ma_demande(
    payload: DemandeCreate,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Crée une demande depuis le formulaire employé."""
    employe = _get_employe(current_user, db)
    try:
        return creer_demande(db, employe.id, payload.type, payload.donnees)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/mes-demandes", response_model=list[DemandeOut])
def mes_demandes(
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)
    return (
        db.query(Demande)
        .filter(Demande.employe_id == employe.id)
        .order_by(Demande.created_at.desc())
        .all()
    )


@router.get("/{demande_id}", response_model=DemandeOut)
def get_demande(
    demande_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)
    demande = db.query(Demande).filter(
        Demande.id == demande_id,
        Demande.employe_id == employe.id,
    ).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    return demande


@router.delete("/{demande_id}", status_code=status.HTTP_204_NO_CONTENT)
def annuler_demande(
    demande_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)
    demande = db.query(Demande).filter(
        Demande.id == demande_id,
        Demande.employe_id == employe.id,
    ).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut != "en_attente":
        raise HTTPException(status_code=400, detail="Seules les demandes en attente peuvent être annulées")
    demande.statut = "annulee"
    db.commit()
