"""CRM — Sociétés, Projets et Affectations.

Réservé au RH et à l'administrateur : ces données pilotent le rattachement des
feuilles de temps aux projets.
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.crm import (
    STATUTS_PROJET, TYPES_MISSION, TYPES_SOCIETE, Affectation, Projet, Societe,
)
from app.models.employee import Employe
from app.models.user import Utilisateur
from app.services.audit import log_action
from app.services.auth import require_rh

router = APIRouter()


# ─── Schémas ─────────────────────────────────────────────────────────────────

class SocieteIn(BaseModel):
    nom: str
    type: str = "client"
    secteur: Optional[str] = None
    ice: Optional[str] = None
    rc: Optional[str] = None
    ville: Optional[str] = None
    pays: Optional[str] = "Maroc"
    site_web: Optional[str] = None
    telephone: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None


class ProjetIn(BaseModel):
    reference: Optional[str] = None      # généré si absent (PRJ###)
    nom: str
    societe_id: Optional[int] = None
    type_mission: str = "regie"
    statut: str = "en_cours"
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
    manager_id: Optional[int] = None
    contact_client: Optional[str] = None
    notes: Optional[str] = None


class AffectationIn(BaseModel):
    employe_id: int
    date_debut: Optional[date] = None
    date_fin: Optional[date] = None
    tjm: Optional[float] = None


# ─── Sérialisation ───────────────────────────────────────────────────────────

def _societe_dict(s: Societe, nb_projets: int | None = None) -> dict:
    return {
        "id": s.id, "nom": s.nom, "type": s.type, "secteur": s.secteur,
        "ice": s.ice, "rc": s.rc, "ville": s.ville, "pays": s.pays,
        "site_web": s.site_web, "telephone": s.telephone, "email": s.email,
        "notes": s.notes,
        "nb_projets": nb_projets if nb_projets is not None else len(s.projets),
    }


def _projet_dict(p: Projet, db: Session | None = None) -> dict:
    manager = None
    if p.manager_id and db is not None:
        u = db.query(Utilisateur).filter(Utilisateur.id == p.manager_id).first()
        if u:
            manager = f"{(u.prenom + ' ') if u.prenom else ''}{u.nom}".strip()
    return {
        "id": p.id, "reference": p.reference, "nom": p.nom, "libelle": p.libelle,
        "societe_id": p.societe_id,
        "societe_nom": p.societe.nom if p.societe else None,
        "type_mission": p.type_mission, "statut": p.statut,
        "date_debut": p.date_debut.isoformat() if p.date_debut else None,
        "date_fin": p.date_fin.isoformat() if p.date_fin else None,
        "manager_id": p.manager_id, "manager_nom": manager,
        "contact_client": p.contact_client, "notes": p.notes,
        "nb_affectations": len([a for a in p.affectations if a.actif]),
    }


def _generer_reference(db: Session) -> str:
    """PRJ### séquentiel, comme les références Boond."""
    import re
    maxi = 0
    for (ref,) in db.query(Projet.reference).all():
        m = re.match(r"^PRJ0*(\d+)$", (ref or "").strip(), re.IGNORECASE)
        if m:
            maxi = max(maxi, int(m.group(1)))
    return f"PRJ{maxi + 1:02d}"


# ─── Sociétés ────────────────────────────────────────────────────────────────

@router.get("/societes")
def liste_societes(
    q: Optional[str] = None,
    type: Optional[str] = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    requete = db.query(Societe)
    if type in TYPES_SOCIETE:
        requete = requete.filter(Societe.type == type)
    if q:
        like = f"%{q.strip()}%"
        requete = requete.filter(or_(
            Societe.nom.ilike(like), Societe.ville.ilike(like), Societe.secteur.ilike(like),
        ))
    societes = requete.order_by(Societe.nom).all()

    # Un seul comptage groupé plutôt qu'une requête par société
    comptes = dict(
        db.query(Projet.societe_id, func.count(Projet.id)).group_by(Projet.societe_id).all()
    )
    return [_societe_dict(s, comptes.get(s.id, 0)) for s in societes]


@router.get("/societes/stats")
def stats_societes(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    par_type = dict(db.query(Societe.type, func.count(Societe.id)).group_by(Societe.type).all())
    return {
        "total": sum(par_type.values()),
        "clients": par_type.get("client", 0),
        "fournisseurs": par_type.get("fournisseur", 0),
        "partenaires": par_type.get("partenaire", 0),
        "prospects": par_type.get("prospect", 0),
        "projets_actifs": db.query(func.count(Projet.id)).filter(Projet.statut == "en_cours").scalar() or 0,
    }


@router.get("/societes/{societe_id}")
def detail_societe(
    societe_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    s = db.query(Societe).filter(Societe.id == societe_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Société introuvable")
    out = _societe_dict(s)
    out["projets"] = [_projet_dict(p, db) for p in s.projets]
    return out


@router.post("/societes", status_code=status.HTTP_201_CREATED)
def creer_societe(
    payload: SocieteIn,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if payload.type not in TYPES_SOCIETE:
        raise HTTPException(status_code=400, detail=f"Type invalide. Valeurs : {list(TYPES_SOCIETE)}")
    if not payload.nom.strip():
        raise HTTPException(status_code=400, detail="Le nom est requis")
    s = Societe(**payload.model_dump())
    db.add(s)
    log_action(db, current_user, "societe.create", cible_type="societe", cible_libelle=payload.nom)
    db.commit()
    db.refresh(s)
    return _societe_dict(s, 0)


@router.put("/societes/{societe_id}")
def modifier_societe(
    societe_id: int,
    payload: SocieteIn,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    s = db.query(Societe).filter(Societe.id == societe_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Société introuvable")
    if payload.type not in TYPES_SOCIETE:
        raise HTTPException(status_code=400, detail=f"Type invalide. Valeurs : {list(TYPES_SOCIETE)}")
    for champ, valeur in payload.model_dump().items():
        setattr(s, champ, valeur)
    log_action(db, current_user, "societe.update", cible_type="societe",
               cible_id=s.id, cible_libelle=s.nom)
    db.commit()
    db.refresh(s)
    return _societe_dict(s)


@router.delete("/societes/{societe_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_societe(
    societe_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    s = db.query(Societe).filter(Societe.id == societe_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Société introuvable")
    # Refus explicite plutôt qu'une suppression en cascade silencieuse des projets
    if s.projets:
        raise HTTPException(
            status_code=400,
            detail=f"Cette société porte {len(s.projets)} projet(s). Supprimez-les d'abord.",
        )
    libelle = s.nom
    db.delete(s)
    log_action(db, current_user, "societe.delete", cible_type="societe", cible_libelle=libelle)
    db.commit()


# ─── Projets ─────────────────────────────────────────────────────────────────

@router.get("/projets")
def liste_projets(
    q: Optional[str] = None,
    statut: Optional[str] = None,
    societe_id: Optional[int] = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    requete = db.query(Projet)
    if statut in STATUTS_PROJET:
        requete = requete.filter(Projet.statut == statut)
    if societe_id:
        requete = requete.filter(Projet.societe_id == societe_id)
    if q:
        like = f"%{q.strip()}%"
        requete = requete.filter(or_(Projet.reference.ilike(like), Projet.nom.ilike(like)))
    return [_projet_dict(p, db) for p in requete.order_by(Projet.reference.desc()).all()]


@router.post("/projets", status_code=status.HTTP_201_CREATED)
def creer_projet(
    payload: ProjetIn,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if payload.type_mission not in TYPES_MISSION:
        raise HTTPException(status_code=400, detail=f"Type de mission invalide. Valeurs : {list(TYPES_MISSION)}")
    if payload.statut not in STATUTS_PROJET:
        raise HTTPException(status_code=400, detail=f"Statut invalide. Valeurs : {list(STATUTS_PROJET)}")
    if not payload.nom.strip():
        raise HTTPException(status_code=400, detail="Le nom du projet est requis")
    if payload.societe_id and not db.query(Societe).filter(Societe.id == payload.societe_id).first():
        raise HTTPException(status_code=404, detail="Société introuvable")

    donnees = payload.model_dump()
    donnees["reference"] = (donnees.get("reference") or "").strip() or _generer_reference(db)
    if db.query(Projet).filter(Projet.reference == donnees["reference"]).first():
        raise HTTPException(status_code=400, detail="Cette référence de projet existe déjà")

    p = Projet(**donnees)
    db.add(p)
    try:
        log_action(db, current_user, "projet.create", cible_type="projet",
                   cible_libelle=f"{p.reference} — {p.nom}")
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Référence de projet déjà utilisée")
    db.refresh(p)
    return _projet_dict(p, db)


@router.get("/projets/{projet_id}")
def detail_projet(
    projet_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    p = db.query(Projet).filter(Projet.id == projet_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Projet introuvable")
    out = _projet_dict(p, db)
    out["affectations"] = [
        {
            "id": a.id, "employe_id": a.employe_id,
            "nom": (
                f"{(a_emp.utilisateur.prenom + ' ') if a_emp and a_emp.utilisateur and a_emp.utilisateur.prenom else ''}"
                f"{a_emp.utilisateur.nom if a_emp and a_emp.utilisateur else '—'}"
            ).strip(),
            "matricule": a_emp.matricule if a_emp else None,
            "poste": a_emp.poste if a_emp else None,
            "date_debut": a.date_debut.isoformat() if a.date_debut else None,
            "date_fin": a.date_fin.isoformat() if a.date_fin else None,
            "tjm": float(a.tjm) if a.tjm is not None else None,
            "actif": a.actif,
        }
        for a in p.affectations
        for a_emp in [db.query(Employe).filter(Employe.id == a.employe_id).first()]
    ]
    return out


@router.put("/projets/{projet_id}")
def modifier_projet(
    projet_id: int,
    payload: ProjetIn,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    p = db.query(Projet).filter(Projet.id == projet_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Projet introuvable")
    if payload.type_mission not in TYPES_MISSION:
        raise HTTPException(status_code=400, detail="Type de mission invalide")
    if payload.statut not in STATUTS_PROJET:
        raise HTTPException(status_code=400, detail="Statut invalide")

    donnees = payload.model_dump()
    nouvelle_ref = (donnees.pop("reference", None) or "").strip()
    if nouvelle_ref and nouvelle_ref != p.reference:
        if db.query(Projet).filter(Projet.reference == nouvelle_ref, Projet.id != projet_id).first():
            raise HTTPException(status_code=400, detail="Cette référence est déjà utilisée")
        p.reference = nouvelle_ref
    for champ, valeur in donnees.items():
        setattr(p, champ, valeur)

    log_action(db, current_user, "projet.update", cible_type="projet",
               cible_id=p.id, cible_libelle=f"{p.reference} — {p.nom}")
    db.commit()
    db.refresh(p)
    return _projet_dict(p, db)


@router.delete("/projets/{projet_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_projet(
    projet_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.models.pointage import Pointage

    p = db.query(Projet).filter(Projet.id == projet_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Projet introuvable")
    # Un projet déjà pointé ne doit pas disparaître : l'historique serait faussé
    saisies = db.query(func.count(Pointage.id)).filter(Pointage.projet_id == projet_id).scalar() or 0
    if saisies:
        raise HTTPException(
            status_code=400,
            detail=f"{saisies} jour(s) déjà pointé(s) sur ce projet. Archivez-le plutôt que de le supprimer.",
        )
    libelle = f"{p.reference} — {p.nom}"
    db.delete(p)
    log_action(db, current_user, "projet.delete", cible_type="projet", cible_libelle=libelle)
    db.commit()


# ─── Affectations ────────────────────────────────────────────────────────────

@router.post("/projets/{projet_id}/affectations", status_code=status.HTTP_201_CREATED)
def affecter(
    projet_id: int,
    payload: AffectationIn,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    p = db.query(Projet).filter(Projet.id == projet_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Projet introuvable")
    emp = db.query(Employe).filter(Employe.id == payload.employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Salarié introuvable")
    existante = db.query(Affectation).filter(
        Affectation.projet_id == projet_id,
        Affectation.employe_id == payload.employe_id,
        Affectation.actif == True,  # noqa: E712
    ).first()
    if existante:
        raise HTTPException(status_code=400, detail="Ce salarié est déjà affecté à ce projet")

    a = Affectation(projet_id=projet_id, **payload.model_dump())
    db.add(a)
    log_action(db, current_user, "projet.affectation", cible_type="projet", cible_id=projet_id,
               cible_libelle=f"{p.reference} — {emp.matricule}")
    db.commit()
    db.refresh(a)
    return {"id": a.id, "projet_id": projet_id, "employe_id": a.employe_id}


@router.delete("/affectations/{affectation_id}", status_code=status.HTTP_204_NO_CONTENT)
def retirer_affectation(
    affectation_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.models.pointage import Pointage

    a = db.query(Affectation).filter(Affectation.id == affectation_id).first()
    if not a:
        raise HTTPException(status_code=404, detail="Affectation introuvable")

    # Si du temps a déjà été saisi, on désactive au lieu de supprimer :
    # l'historique de pointage doit rester cohérent.
    deja_pointe = db.query(func.count(Pointage.id)).filter(
        Pointage.projet_id == a.projet_id, Pointage.employe_id == a.employe_id
    ).scalar() or 0
    if deja_pointe:
        a.actif = False
    else:
        db.delete(a)
    db.commit()


@router.get("/mes-projets")
def mes_projets(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Projets actifs, pour alimenter les listes déroulantes."""
    projets = db.query(Projet).filter(Projet.statut == "en_cours").order_by(Projet.reference).all()
    return [{"id": p.id, "libelle": p.libelle, "reference": p.reference} for p in projets]
