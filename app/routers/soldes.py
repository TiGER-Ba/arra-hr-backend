from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.employee import Employe
from app.models.rh import RH
from app.models.solde import SOLDE_TYPES, MouvementSolde, SoldeEmploye
from app.models.user import Utilisateur
from app.schemas.solde import MouvementOut, SoldeAjuster, SoldeCreate, SoldeDetail
from app.services.auth import get_current_user, require_rh
from app.services.soldes import ajuster_solde, initialiser_soldes_par_defaut

router = APIRouter()


def _solde_to_detail(s: SoldeEmploye) -> dict:
    config = SOLDE_TYPES.get(s.type, {})
    quota = float(s.quota_total)
    consomme = float(s.consomme)
    return {
        "id": s.id,
        "employe_id": s.employe_id,
        "type": s.type,
        "unite": s.unite,
        "quota_total": quota,
        "consomme": consomme,
        "annee_reference": s.annee_reference,
        "updated_at": s.updated_at,
        "label": config.get("label", s.type),
        "reste": quota - consomme,
    }


# ─── Employé : ses propres soldes ────────────────────────────────────────────

@router.get("/mes-soldes", response_model=list[SoldeDetail])
def mes_soldes(
    annee: int | None = None,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    emp = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Profil employé introuvable")
    annee = annee or datetime.now().year
    # Initialise les soldes manquants automatiquement
    initialiser_soldes_par_defaut(db, emp.id, annee)
    soldes = db.query(SoldeEmploye).filter(
        SoldeEmploye.employe_id == emp.id,
        SoldeEmploye.annee_reference == annee,
    ).all()
    return [_solde_to_detail(s) for s in soldes]


# ─── RH : soldes d'un employé ────────────────────────────────────────────────

@router.get("/employe/{employe_id}", response_model=list[SoldeDetail])
def soldes_employe(
    employe_id: int,
    annee: int | None = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    annee = annee or datetime.now().year
    initialiser_soldes_par_defaut(db, emp.id, annee)
    soldes = db.query(SoldeEmploye).filter(
        SoldeEmploye.employe_id == employe_id,
        SoldeEmploye.annee_reference == annee,
    ).all()
    return [_solde_to_detail(s) for s in soldes]


@router.post("/employe/{employe_id}", response_model=SoldeDetail, status_code=status.HTTP_201_CREATED)
def creer_solde(
    employe_id: int,
    payload: SoldeCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if payload.type not in SOLDE_TYPES:
        raise HTTPException(status_code=400, detail=f"Type invalide. Valeurs: {list(SOLDE_TYPES.keys())}")
    annee = payload.annee_reference or datetime.now().year
    existing = db.query(SoldeEmploye).filter(
        SoldeEmploye.employe_id == employe_id,
        SoldeEmploye.type == payload.type,
        SoldeEmploye.annee_reference == annee,
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Ce solde existe déjà pour cette année")
    config = SOLDE_TYPES[payload.type]
    solde = SoldeEmploye(
        employe_id=employe_id,
        type=payload.type,
        unite=config["unite"],
        quota_total=payload.quota_total,
        consomme=0,
        annee_reference=annee,
    )
    db.add(solde)
    db.commit()
    db.refresh(solde)
    return _solde_to_detail(solde)


@router.patch("/{solde_id}/quota", response_model=SoldeDetail)
def modifier_quota(
    solde_id: int,
    nouveau_quota: float,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    solde = db.query(SoldeEmploye).filter(SoldeEmploye.id == solde_id).first()
    if not solde:
        raise HTTPException(status_code=404, detail="Solde introuvable")
    solde.quota_total = nouveau_quota
    db.commit()
    db.refresh(solde)
    return _solde_to_detail(solde)


@router.post("/{solde_id}/ajuster", response_model=SoldeDetail)
def ajuster(
    solde_id: int,
    payload: SoldeAjuster,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    solde = db.query(SoldeEmploye).filter(SoldeEmploye.id == solde_id).first()
    if not solde:
        raise HTTPException(status_code=404, detail="Solde introuvable")
    rh = db.query(RH).filter(RH.utilisateur_id == current_user.id).first()
    ajuster_solde(db, solde, payload.delta, payload.motif, rh_id=rh.id if rh else None)
    return _solde_to_detail(solde)


@router.post("/{solde_id}/reinitialiser", response_model=SoldeDetail)
def reinitialiser(
    solde_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Remet à zéro la consommation du solde (pour corriger une erreur)."""
    solde = db.query(SoldeEmploye).filter(SoldeEmploye.id == solde_id).first()
    if not solde:
        raise HTTPException(status_code=404, detail="Solde introuvable")
    rh = db.query(RH).filter(RH.utilisateur_id == current_user.id).first()
    ancien = float(solde.consomme)
    if ancien != 0:
        db.add(MouvementSolde(
            solde_id=solde.id,
            delta=ancien,
            motif=f"Réinitialisation par RH (consommé remis à 0, ancien: {ancien:g})",
            cree_par_rh_id=rh.id if rh else None,
        ))
    solde.consomme = 0
    db.commit()
    db.refresh(solde)
    return _solde_to_detail(solde)


@router.get("/{solde_id}/mouvements", response_model=list[MouvementOut])
def mouvements_solde(
    solde_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    mvts = (
        db.query(MouvementSolde)
        .filter(MouvementSolde.solde_id == solde_id)
        .order_by(MouvementSolde.created_at.desc())
        .all()
    )
    return mvts
