"""Gestion des utilisateurs — création/administration des accès.

Un seul endroit pour créer et gérer les trois types de comptes :
  - admin    : accès complet (hérite des droits RH)
  - rh       : service RH (validation, dépôt, paramétrage…)
  - employe  : espace personnel + chatbot

Réservé aux comptes RH/admin (`require_rh`). Les employés créés ici reçoivent
automatiquement leurs soldes de congés par défaut, comme via la page Employés.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.employee import Employe
from app.models.rh import RH
from app.models.user import Utilisateur
from app.services.auth import get_password_hash, require_rh
from app.services.soldes import initialiser_soldes_par_defaut

router = APIRouter()

VALID_ROLES = {"employe", "rh", "admin"}
MIN_PASSWORD_LEN = 6


# ─── Schemas ────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    nom: str
    email: EmailStr
    mot_de_passe: str
    role: str  # employe | rh | admin
    # RH
    service: Optional[str] = None
    # Employé
    matricule: Optional[str] = None
    poste: Optional[str] = None
    departement: Optional[str] = None
    salaire_base: Optional[float] = None
    date_embauche: Optional[date] = None
    type_contrat: Optional[str] = "CDI"
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


class UserUpdate(BaseModel):
    nom: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    service: Optional[str] = None  # role rh uniquement


class PasswordReset(BaseModel):
    mot_de_passe: str


# ─── Helpers ────────────────────────────────────────────────────────────────

def _user_to_dict(u: Utilisateur) -> dict:
    d = {
        "id": u.id,
        "nom": u.nom,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }
    if u.role == "employe" and u.employe:
        d["matricule"] = u.employe.matricule
        d["poste"] = u.employe.poste
        d["departement"] = u.employe.departement
        d["employe_id"] = u.employe.id
    elif u.role == "rh" and u.rh:
        d["service"] = u.rh.service
    return d


def _count_active_admins_rh(db: Session, exclude_id: int | None = None) -> int:
    q = db.query(func.count(Utilisateur.id)).filter(
        Utilisateur.role.in_(("rh", "admin")),
        Utilisateur.is_active == True,  # noqa: E712
    )
    if exclude_id is not None:
        q = q.filter(Utilisateur.id != exclude_id)
    return q.scalar() or 0


# ─── Endpoints ──────────────────────────────────────────────────────────────

@router.get("")
def liste_utilisateurs(
    role: Optional[str] = None,
    q: Optional[str] = None,
    actif: Optional[bool] = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    query = db.query(Utilisateur)
    if role in VALID_ROLES:
        query = query.filter(Utilisateur.role == role)
    if actif is not None:
        query = query.filter(Utilisateur.is_active == actif)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(Utilisateur.nom.ilike(like), Utilisateur.email.ilike(like)))
    users = query.order_by(Utilisateur.created_at.desc()).all()
    return [_user_to_dict(u) for u in users]


@router.get("/stats")
def stats_utilisateurs(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    def _count(**flt):
        return db.query(func.count(Utilisateur.id)).filter_by(**flt).scalar() or 0

    return {
        "total": _count(),
        "actifs": _count(is_active=True),
        "admins": _count(role="admin"),
        "rh": _count(role="rh"),
        "employes": _count(role="employe"),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
def creer_utilisateur(
    payload: UserCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Rôle invalide. Valeurs : {sorted(VALID_ROLES)}")
    if len(payload.mot_de_passe) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")
    if db.query(Utilisateur).filter(Utilisateur.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")

    if payload.role == "employe":
        missing = [f for f in ("matricule", "poste", "departement", "salaire_base", "date_embauche")
                   if getattr(payload, f) in (None, "")]
        if missing:
            raise HTTPException(status_code=400, detail=f"Champs employé requis : {', '.join(missing)}")
        if db.query(Employe).filter(Employe.matricule == payload.matricule).first():
            raise HTTPException(status_code=400, detail="Ce matricule est déjà utilisé")

    user = Utilisateur(
        nom=payload.nom,
        email=payload.email,
        mot_de_passe=get_password_hash(payload.mot_de_passe),
        role=payload.role,
    )
    db.add(user)
    db.flush()

    if payload.role == "employe":
        emp = Employe(
            utilisateur_id=user.id,
            matricule=payload.matricule,
            poste=payload.poste,
            departement=payload.departement,
            salaire_base=payload.salaire_base,
            date_embauche=payload.date_embauche,
            type_contrat=payload.type_contrat or "CDI",
            cin=payload.cin,
            cnss=payload.cnss,
            adresse=payload.adresse,
            telephone=payload.telephone,
        )
        db.add(emp)
        db.flush()
        initialiser_soldes_par_defaut(db, emp.id)
    elif payload.role == "rh":
        db.add(RH(utilisateur_id=user.id, service=payload.service or "Ressources Humaines"))

    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


@router.put("/{user_id}")
def modifier_utilisateur(
    user_id: int,
    payload: UserUpdate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")

    if payload.nom is not None:
        user.nom = payload.nom
    if payload.email is not None and payload.email != user.email:
        if db.query(Utilisateur).filter(Utilisateur.email == payload.email, Utilisateur.id != user_id).first():
            raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")
        user.email = payload.email
    if payload.service is not None and user.role == "rh" and user.rh:
        user.rh.service = payload.service
    if payload.is_active is not None and payload.is_active != user.is_active:
        _guard_deactivation(user, payload.is_active, current_user, db)
        user.is_active = payload.is_active

    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


@router.patch("/{user_id}/toggle-active")
def basculer_activation(
    user_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    nouveau = not user.is_active
    _guard_deactivation(user, nouveau, current_user, db)
    user.is_active = nouveau
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


@router.post("/{user_id}/password")
def reinitialiser_mot_de_passe(
    user_id: int,
    payload: PasswordReset,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    if len(payload.mot_de_passe) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")
    user.mot_de_passe = get_password_hash(payload.mot_de_passe)
    db.commit()
    return {"message": "Mot de passe réinitialisé"}


def _guard_deactivation(user: Utilisateur, nouvel_etat_actif: bool, current_user: Utilisateur, db: Session):
    """Empêche de se verrouiller soi-même ou de retirer le dernier accès RH/admin."""
    if nouvel_etat_actif:
        return  # réactivation : toujours autorisée
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas désactiver votre propre compte")
    if user.role in ("rh", "admin") and _count_active_admins_rh(db, exclude_id=user.id) == 0:
        raise HTTPException(status_code=400, detail="Au moins un compte RH/admin actif doit rester")
