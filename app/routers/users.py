"""Gestion des utilisateurs — création/administration des accès.

Un seul endroit pour créer et gérer les trois types de comptes :
  - admin    : accès complet (gère TOUS les utilisateurs + signature/cachet/API)
  - rh       : gère uniquement les EMPLOYÉS (créer / modifier / supprimer / désactiver)
  - employe  : espace personnel + chatbot (self-service : mot de passe + infos perso)

Règles d'accès :
  - admin → peut gérer n'importe quel compte.
  - rh    → peut gérer uniquement les comptes de rôle « employe ».
  - chaque utilisateur → peut modifier son propre mot de passe et ses infos perso.

Email auto : {prénom[0]}.{nom}@{EMAIL_DOMAIN} (ex. w.baba@arra-engineering.com).
Matricule auto : EMP### séquentiel.
"""
import re
import unicodedata
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.employee import Employe
from app.models.rh import RH
from app.models.user import Utilisateur
from app.services.auth import get_current_user, get_password_hash, require_rh, verify_password
from app.services.soldes import initialiser_soldes_par_defaut

router = APIRouter()

VALID_ROLES = {"employe", "rh", "admin"}
MIN_PASSWORD_LEN = 6


# ─── Schemas ────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    nom: str
    prenom: Optional[str] = None
    email: Optional[EmailStr] = None  # auto-généré si absent
    mot_de_passe: str
    role: str  # employe | rh | admin
    service: Optional[str] = None                # RH
    matricule: Optional[str] = None              # employé (auto si absent)
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
    prenom: Optional[str] = None
    email: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    service: Optional[str] = None                 # RH
    poste: Optional[str] = None                   # employé
    departement: Optional[str] = None
    salaire_base: Optional[float] = None
    date_embauche: Optional[date] = None
    type_contrat: Optional[str] = None
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


class PasswordReset(BaseModel):
    mot_de_passe: str


class SelfProfileUpdate(BaseModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


class SelfPassword(BaseModel):
    ancien_mot_de_passe: str
    nouveau_mot_de_passe: str


# ─── Helpers : normalisation, email & matricule auto ────────────────────────

def _slug(s: str | None) -> str:
    """minuscule, sans accents, uniquement [a-z0-9]."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def generate_email(prenom: str | None, nom: str | None, db: Session) -> str:
    """{prénom[0]}.{nom}@domaine, suffixe chiffré si collision."""
    p = _slug(prenom)
    n = _slug(nom)
    if p and n:
        base = f"{p[0]}.{n}"
    elif n:
        base = n
    elif p:
        base = p
    else:
        base = "utilisateur"
    domain = settings.EMAIL_DOMAIN
    candidate = f"{base}@{domain}"
    i = 1
    while db.query(Utilisateur).filter(Utilisateur.email == candidate).first():
        i += 1
        candidate = f"{base}{i}@{domain}"
    return candidate


def generate_matricule(db: Session) -> str:
    """EMP### séquentiel = max(suffixe numérique) + 1."""
    rows = db.query(Employe.matricule).all()
    max_n = 0
    for (m,) in rows:
        match = re.match(r"^EMP0*(\d+)$", (m or "").strip(), re.IGNORECASE)
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"EMP{max_n + 1:03d}"


def _user_to_dict(u: Utilisateur) -> dict:
    d = {
        "id": u.id,
        "nom": u.nom,
        "prenom": u.prenom,
        "email": u.email,
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }
    if u.role == "employe" and u.employe:
        e = u.employe
        d.update({
            "employe_id": e.id, "matricule": e.matricule, "poste": e.poste,
            "departement": e.departement, "salaire_base": float(e.salaire_base),
            "date_embauche": e.date_embauche.isoformat() if e.date_embauche else None,
            "statut": e.statut, "type_contrat": e.type_contrat,
            "cin": e.cin, "cnss": e.cnss, "adresse": e.adresse, "telephone": e.telephone,
        })
    elif u.role == "rh" and u.rh:
        d["service"] = u.rh.service
    return d


def _assert_can_manage(actor: Utilisateur, target_role: str):
    """admin gère tout ; rh gère uniquement les employés."""
    if actor.role == "admin":
        return
    if actor.role == "rh" and target_role == "employe":
        return
    raise HTTPException(status_code=403, detail="Vous ne pouvez gérer que les comptes employés")


def _count_active_admins_rh(db: Session, exclude_id: int | None = None) -> int:
    q = db.query(func.count(Utilisateur.id)).filter(
        Utilisateur.role.in_(("rh", "admin")),
        Utilisateur.is_active == True,  # noqa: E712
    )
    if exclude_id is not None:
        q = q.filter(Utilisateur.id != exclude_id)
    return q.scalar() or 0


def _guard_deactivation(user: Utilisateur, nouvel_etat_actif: bool, current_user: Utilisateur, db: Session):
    if nouvel_etat_actif:
        return
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas désactiver votre propre compte")
    if user.role in ("rh", "admin") and _count_active_admins_rh(db, exclude_id=user.id) == 0:
        raise HTTPException(status_code=400, detail="Au moins un compte RH/admin actif doit rester")


# ─── Self-service (tout utilisateur connecté) ───────────────────────────────

@router.get("/me/profile")
def mon_profil(
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _user_to_dict(current_user)


@router.put("/me/profile")
def modifier_mon_profil(
    payload: SelfProfileUpdate,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.nom is not None:
        current_user.nom = payload.nom
    if payload.prenom is not None:
        current_user.prenom = payload.prenom
    # Champs employé (infos personnelles uniquement — pas poste/salaire/matricule)
    if current_user.role == "employe" and current_user.employe:
        e = current_user.employe
        if payload.cin is not None:
            e.cin = payload.cin
        if payload.cnss is not None:
            e.cnss = payload.cnss
        if payload.adresse is not None:
            e.adresse = payload.adresse
        if payload.telephone is not None:
            e.telephone = payload.telephone
    db.commit()
    db.refresh(current_user)
    return _user_to_dict(current_user)


@router.post("/me/password")
def changer_mon_mot_de_passe(
    payload: SelfPassword,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(payload.ancien_mot_de_passe, current_user.mot_de_passe):
        raise HTTPException(status_code=400, detail="Mot de passe actuel incorrect")
    if len(payload.nouveau_mot_de_passe) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Le nouveau mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")
    current_user.mot_de_passe = get_password_hash(payload.nouveau_mot_de_passe)
    db.commit()
    return {"message": "Mot de passe modifié"}


# ─── Administration (rh = employés / admin = tout) ──────────────────────────

@router.get("")
def liste_utilisateurs(
    role: Optional[str] = None,
    q: Optional[str] = None,
    actif: Optional[bool] = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    query = db.query(Utilisateur)
    # Un RH ne voit/gère que les employés
    if current_user.role != "admin":
        query = query.filter(Utilisateur.role == "employe")
    elif role in VALID_ROLES:
        query = query.filter(Utilisateur.role == role)
    if actif is not None:
        query = query.filter(Utilisateur.is_active == actif)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            Utilisateur.nom.ilike(like), Utilisateur.prenom.ilike(like), Utilisateur.email.ilike(like),
        ))
    users = query.order_by(Utilisateur.created_at.desc()).all()
    return [_user_to_dict(u) for u in users]


@router.get("/stats")
def stats_utilisateurs(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    def _count(**flt):
        return db.query(func.count(Utilisateur.id)).filter_by(**flt).scalar() or 0

    if current_user.role != "admin":
        return {
            "total": _count(role="employe"),
            "actifs": db.query(func.count(Utilisateur.id)).filter(
                Utilisateur.role == "employe", Utilisateur.is_active == True).scalar() or 0,  # noqa: E712
            "admins": 0, "rh": 0, "employes": _count(role="employe"),
        }
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
    _assert_can_manage(current_user, payload.role)
    if len(payload.mot_de_passe) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")

    # Email : fourni (admin) ou auto-généré
    email = str(payload.email) if payload.email else generate_email(payload.prenom, payload.nom, db)
    if db.query(Utilisateur).filter(Utilisateur.email == email).first():
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")

    if payload.role == "employe":
        for f in ("poste", "departement", "salaire_base", "date_embauche"):
            if getattr(payload, f) in (None, ""):
                raise HTTPException(status_code=400, detail=f"Champ employé requis : {f}")
        matricule = (payload.matricule or "").strip() or generate_matricule(db)
        if db.query(Employe).filter(Employe.matricule == matricule).first():
            raise HTTPException(status_code=400, detail="Ce matricule est déjà utilisé")

    user = Utilisateur(
        nom=payload.nom,
        prenom=payload.prenom,
        email=email,
        mot_de_passe=get_password_hash(payload.mot_de_passe),
        role=payload.role,
    )
    db.add(user)
    db.flush()

    if payload.role == "employe":
        emp = Employe(
            utilisateur_id=user.id,
            matricule=matricule,
            poste=payload.poste,
            departement=payload.departement,
            salaire_base=payload.salaire_base,
            date_embauche=payload.date_embauche,
            type_contrat=payload.type_contrat or "CDI",
            cin=payload.cin, cnss=payload.cnss, adresse=payload.adresse, telephone=payload.telephone,
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
    _assert_can_manage(current_user, user.role)

    if payload.nom is not None:
        user.nom = payload.nom
    if payload.prenom is not None:
        user.prenom = payload.prenom
    if payload.email is not None and str(payload.email) != user.email:
        if db.query(Utilisateur).filter(Utilisateur.email == str(payload.email), Utilisateur.id != user_id).first():
            raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")
        user.email = str(payload.email)
    if payload.is_active is not None and payload.is_active != user.is_active:
        _guard_deactivation(user, payload.is_active, current_user, db)
        user.is_active = payload.is_active
    if payload.service is not None and user.role == "rh" and user.rh:
        user.rh.service = payload.service
    if user.role == "employe" and user.employe:
        e = user.employe
        for attr in ("poste", "departement", "salaire_base", "date_embauche",
                     "type_contrat", "cin", "cnss", "adresse", "telephone"):
            val = getattr(payload, attr)
            if val is not None:
                setattr(e, attr, val)

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
    _assert_can_manage(current_user, user.role)
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
    _assert_can_manage(current_user, user.role)
    if len(payload.mot_de_passe) < MIN_PASSWORD_LEN:
        raise HTTPException(status_code=400, detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")
    user.mot_de_passe = get_password_hash(payload.mot_de_passe)
    db.commit()
    return {"message": "Mot de passe réinitialisé"}


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_utilisateur(
    user_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    _assert_can_manage(current_user, user.role)
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Vous ne pouvez pas supprimer votre propre compte")
    if user.role in ("rh", "admin") and _count_active_admins_rh(db, exclude_id=user.id) == 0:
        raise HTTPException(status_code=400, detail="Au moins un compte RH/admin actif doit rester")

    try:
        if user.role == "employe" and user.employe:
            db.delete(user.employe)
        elif user.role == "rh" and user.rh:
            db.delete(user.rh)
        db.delete(user)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Suppression impossible (documents/demandes liés). Désactivez plutôt le compte.",
        )


# ─── Extraction pièce d'identité (OCR local, sans LLM) ──────────────────────

@router.post("/extract-id")
async def extraire_piece_identite(
    fichier: UploadFile = File(...),
    current_user: Utilisateur = Depends(require_rh),
):
    """OCR local (MRZ + Tesseract) d'une CIN/passeport → champs pré-remplis.

    Aucune donnée n'est envoyée à un service externe (confidentialité).
    """
    data = await fichier.read()
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide")
    try:
        from app.services.id_ocr import extract_id_fields
    except Exception:
        raise HTTPException(status_code=503, detail="Module OCR indisponible sur le serveur")
    return extract_id_fields(data, fichier.filename or "")
