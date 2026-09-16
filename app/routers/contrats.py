"""Édition des contrats depuis la fiche d'un salarié.

Le type de contrat choisit le modèle ; le numéro vient du registre. Le PDF est
produit à la volée, comme le CRA — il n'est pas stocké, seul le NUMÉRO l'est,
pour qu'une réimpression donne le même contrat.
"""
import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.contrat import Contrat
from app.models.employee import Employe
from app.models.user import Utilisateur
from app.services.audit import log_action
from app.services.auth import require_rh
from app.services.contrats import (
    LIBELLES, ContratIndisponible, generer_pdf_contrat, modele_pour,
)

router = APIRouter()


@router.get("/disponibilite/{employe_id}")
def disponibilite(
    employe_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Indique si un contrat peut être édité, et sinon pourquoi.

    Sert à l'interface pour désactiver le bouton avec un motif lisible plutôt
    que de laisser l'utilisateur découvrir l'erreur après le clic.
    """
    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Salarié introuvable")

    type_modele = modele_pour(emp.type_contrat)
    contrat = db.query(Contrat).filter_by(
        employe_id=emp.id, type_contrat=emp.type_contrat
    ).first()

    if not type_modele:
        return {
            "disponible": False,
            "type_contrat": emp.type_contrat,
            "motif": f"Aucun modèle de contrat pour « {emp.type_contrat or 'non défini'} ». "
                     "Ajoutez-en un depuis Paramétrage › Modèles de documents.",
            "numero": None,
        }

    return {
        "disponible": True,
        "type_contrat": emp.type_contrat,
        "modele": type_modele,
        "libelle": LIBELLES.get(type_modele, type_modele),
        "numero": contrat.numero if contrat else None,
    }


@router.get("/{employe_id}")
def generer_contrat(
    employe_id: int,
    inline: bool = False,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Produit le contrat au format PDF, en attribuant son numéro au besoin."""
    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Salarié introuvable")

    try:
        pdf, fichier = generer_pdf_contrat(db, emp, current_user.id)
    except ContratIndisponible as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_action(db, current_user, "contrat.generate", cible_type="employe",
               cible_id=emp.id, cible_libelle=emp.matricule,
               details=f"{emp.type_contrat}")
    db.commit()

    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{fichier}"'},
    )
