from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.employee import Employe
from app.models.rh import RH
from app.models.user import Utilisateur
from app.schemas.user import EmployeCreate, Token, UtilisateurCreate, UtilisateurLogin, UtilisateurOut
from app.services.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    require_rh,
    verify_password,
)

router = APIRouter()

VALID_ROLES = {"employe", "rh", "admin"}
MIN_PASSWORD_LEN = 6


class InvitationSetPassword(BaseModel):
    mot_de_passe: str


def _get_valid_invite(token: str, db: Session) -> Utilisateur:
    user = db.query(Utilisateur).filter(Utilisateur.invite_token == token).first()
    if not user or not token:
        raise HTTPException(status_code=404, detail="Lien d'invitation invalide")
    expire = user.invite_token_expire
    if expire is None:
        raise HTTPException(status_code=400, detail="Lien d'invitation invalide")
    if expire.tzinfo is None:
        expire = expire.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expire:
        raise HTTPException(status_code=400, detail="Lien d'invitation expiré")
    return user


@router.post("/register", response_model=Token, status_code=status.HTTP_201_CREATED)
def register(
    payload: UtilisateurCreate,
    extra: EmployeCreate | None = None,
    current_user: Utilisateur = Depends(require_rh),  # ⚠️ plus d'inscription anonyme
    db: Session = Depends(get_db),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Rôle invalide. Valeurs acceptées : {VALID_ROLES}")

    if db.query(Utilisateur).filter(Utilisateur.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")

    user = Utilisateur(
        nom=payload.nom,
        email=payload.email,
        mot_de_passe=get_password_hash(payload.mot_de_passe),
        role=payload.role,
    )
    db.add(user)
    db.flush()

    if payload.role == "employe":
        if extra is None:
            raise HTTPException(status_code=400, detail="Les informations employé sont requises")
        emp = Employe(
            utilisateur_id=user.id,
            matricule=extra.matricule,
            poste=extra.poste,
            departement=extra.departement,
            salaire_base=extra.salaire_base,
            date_embauche=date.fromisoformat(extra.date_embauche),
        )
        db.add(emp)
    elif payload.role == "rh":
        service = extra.service_rh if extra and extra.service_rh else "Ressources Humaines"
        rh = RH(utilisateur_id=user.id, service=service)
        db.add(rh)

    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return Token(access_token=token, token_type="bearer", user=UtilisateurOut.model_validate(user))


@router.post("/login", response_model=Token)
def login(payload: UtilisateurLogin, db: Session = Depends(get_db)):
    user = db.query(Utilisateur).filter(Utilisateur.email == payload.email).first()
    if not user or not verify_password(payload.mot_de_passe, user.mot_de_passe):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Email ou mot de passe incorrect")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Compte désactivé")

    token = create_access_token({"sub": str(user.id), "role": user.role})
    return Token(access_token=token, token_type="bearer", user=UtilisateurOut.model_validate(user))


@router.get("/me", response_model=UtilisateurOut)
def me(current_user: Utilisateur = Depends(get_current_user)):
    return current_user


# ─── Invitation par email (public : définition du 1er mot de passe) ──────────

@router.get("/invitation/{token}")
def verifier_invitation(token: str, db: Session = Depends(get_db)):
    user = _get_valid_invite(token, db)
    return {"valid": True, "nom": user.nom, "prenom": user.prenom, "email": user.email}


@router.post("/invitation/{token}", response_model=Token)
def accepter_invitation(token: str, payload: InvitationSetPassword, db: Session = Depends(get_db)):
    user = _get_valid_invite(token, db)
    if len(payload.mot_de_passe) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")
    user.mot_de_passe = get_password_hash(payload.mot_de_passe)
    user.invite_token = None
    user.invite_token_expire = None
    user.is_active = True
    db.commit()
    db.refresh(user)
    access = create_access_token({"sub": str(user.id), "role": user.role})
    return Token(access_token=access, token_type="bearer", user=UtilisateurOut.model_validate(user))
