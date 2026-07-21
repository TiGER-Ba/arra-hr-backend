import os

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.demande import Demande
from app.models.document import Document
from app.models.employee import Employe
from app.models.user import Utilisateur
from app.schemas.document import DocumentOut
from app.services.auth import get_current_user

router = APIRouter()


def _resolve_user(request: Request, token_param: str | None, db: Session) -> Utilisateur:
    """Resolve user from Authorization header OR ?token= query param."""
    raw = token_param
    if not raw:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            raw = auth[7:]
    if not raw:
        raise HTTPException(status_code=401, detail="Non authentifié")
    try:
        payload = jwt.decode(raw, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Token invalide")
    except JWTError:
        raise HTTPException(status_code=401, detail="Token invalide ou expiré")
    user = db.query(Utilisateur).filter(Utilisateur.id == int(user_id)).first()
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="Utilisateur introuvable")
    return user


def _check_access(doc: Document, current_user: Utilisateur, db: Session):
    if current_user.role in ("rh", "admin"):
        return
    employe = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
    demande = db.query(Demande).filter(
        Demande.id == doc.demande_id,
        Demande.employe_id == (employe.id if employe else -1),
    ).first()
    if not demande:
        raise HTTPException(status_code=403, detail="Accès refusé")


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")
    _check_access(doc, current_user, db)
    return doc


@router.get("/{document_id}/download")
def download_document(
    document_id: int,
    request: Request,
    token: str | None = Query(default=None),
    inline: bool = Query(default=False),  # True = visualisation dans le navigateur (pas de téléchargement)
    db: Session = Depends(get_db),
):
    current_user = _resolve_user(request, token, db)

    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")

    _check_access(doc, current_user, db)

    if not doc.chemin_fichier or not os.path.exists(doc.chemin_fichier):
        raise HTTPException(status_code=404, detail="Fichier PDF introuvable sur le disque")

    return FileResponse(
        path=doc.chemin_fichier,
        media_type="application/pdf",
        filename=os.path.basename(doc.chemin_fichier),
        content_disposition_type="inline" if inline else "attachment",
    )
