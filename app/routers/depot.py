import os
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.depot_document import CATEGORIES, DepotDocument
from app.models.employee import Employe
from app.models.rh import RH
from app.models.user import Utilisateur
from app.schemas.depot import DepotDocumentDetail, DepotDocumentOut
from app.services.auth import get_current_user, require_rh

router = APIRouter()

DEPOT_DIR = os.path.join(settings.UPLOADS_DIR, "depot")


def _get_rh(user: Utilisateur, db: Session) -> RH:
    rh = db.query(RH).filter(RH.utilisateur_id == user.id).first()
    if not rh:
        raise HTTPException(status_code=404, detail="Profil RH introuvable")
    return rh


def _get_employe_or_404(employe_id: int, db: Session) -> Employe:
    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    return emp


def _doc_to_detail(doc: DepotDocument) -> dict:
    out = DepotDocumentOut.model_validate(doc).model_dump()
    out["nom_employe"] = doc.employe.utilisateur.nom if doc.employe and doc.employe.utilisateur else None
    out["matricule"] = doc.employe.matricule if doc.employe else None
    out["uploaded_by_nom"] = (
        doc.uploaded_by_rh.utilisateur.nom
        if doc.uploaded_by_rh and doc.uploaded_by_rh.utilisateur
        else None
    )
    return out


# ─── RH : upload d'un document dans le dépôt d'un employé ───────────────────

@router.post("/employe/{employe_id}/upload", status_code=status.HTTP_201_CREATED)
async def upload_document(
    employe_id: int,
    fichier: UploadFile = File(...),
    categorie: str = Form(...),
    nom_fichier: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    mois: Optional[str] = Form(None),
    annee: Optional[int] = Form(None),
    visible_employe: bool = Form(True),
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if categorie not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Catégorie invalide. Valeurs: {list(CATEGORIES)}")

    emp = _get_employe_or_404(employe_id, db)
    rh = _get_rh(current_user, db)

    # Dossier dédié à l'employé
    emp_dir = os.path.join(DEPOT_DIR, f"employe_{emp.matricule}")
    os.makedirs(emp_dir, exist_ok=True)

    # Nom de fichier sûr : on garde l'original mais on préfixe
    safe_name = fichier.filename or "document"
    # Éviter les collisions de noms
    base, ext = os.path.splitext(safe_name)
    counter = 1
    dest_path = os.path.join(emp_dir, safe_name)
    while os.path.exists(dest_path):
        dest_path = os.path.join(emp_dir, f"{base}_{counter}{ext}")
        counter += 1

    with open(dest_path, "wb") as f:
        shutil.copyfileobj(fichier.file, f)

    doc = DepotDocument(
        employe_id=emp.id,
        uploaded_by_rh_id=rh.id,
        categorie=categorie,
        nom_fichier=nom_fichier or safe_name,
        chemin_fichier=dest_path,
        description=description,
        mois=mois,
        annee=annee,
        visible_employe=visible_employe,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Notifier l'employé si le doc est visible
    if visible_employe and emp.utilisateur:
        from app.services.notifications import notifier
        notifier(
            db=db,
            utilisateur_id=emp.utilisateur.id,
            type="doc_depose",
            titre="Nouveau document disponible",
            message=f"Un document « {nom_fichier or safe_name} » a été déposé dans votre espace.",
            lien="/employe/mes-documents",
        )

    return _doc_to_detail(doc)


# ─── RH : liste du dépôt d'un employé ───────────────────────────────────────

@router.get("/employe/{employe_id}", response_model=list[DepotDocumentDetail])
def liste_depot_employe(
    employe_id: int,
    categorie: Optional[str] = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    _get_employe_or_404(employe_id, db)
    q = db.query(DepotDocument).filter(DepotDocument.employe_id == employe_id)
    if categorie:
        q = q.filter(DepotDocument.categorie == categorie)
    docs = q.order_by(DepotDocument.uploaded_at.desc()).all()
    return [_doc_to_detail(d) for d in docs]


# ─── RH : modifier visibilité / description d'un document ───────────────────

@router.patch("/document/{doc_id}")
def modifier_document(
    doc_id: int,
    visible_employe: Optional[bool] = None,
    description: Optional[str] = None,
    nom_fichier: Optional[str] = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    doc = db.query(DepotDocument).filter(DepotDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")
    if visible_employe is not None:
        doc.visible_employe = visible_employe
    if description is not None:
        doc.description = description
    if nom_fichier is not None:
        doc.nom_fichier = nom_fichier
    db.commit()
    return _doc_to_detail(doc)


# ─── RH : supprimer un document du dépôt ────────────────────────────────────

@router.delete("/document/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def supprimer_document(
    doc_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    doc = db.query(DepotDocument).filter(DepotDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")
    if os.path.exists(doc.chemin_fichier):
        os.remove(doc.chemin_fichier)
    db.delete(doc)
    db.commit()


# ─── Employé : son propre dépôt ─────────────────────────────────────────────

@router.get("/mes-documents", response_model=list[DepotDocumentDetail])
def mes_documents_depot(
    categorie: Optional[str] = None,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    emp = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Profil employé introuvable")

    q = db.query(DepotDocument).filter(
        DepotDocument.employe_id == emp.id,
        DepotDocument.visible_employe == True,
    )
    if categorie:
        q = q.filter(DepotDocument.categorie == categorie)
    docs = q.order_by(DepotDocument.uploaded_at.desc()).all()
    return [_doc_to_detail(d) for d in docs]


# ─── Téléchargement (RH + employé concerné) — accepte ?token= pour liens directs

def _resolve_user_depot(request: Request, token_param: str | None, db: Session) -> Utilisateur:
    raw = token_param or ""
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


@router.get("/document/{doc_id}/telecharger")
def telecharger_document(
    doc_id: int,
    request: Request,
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    current_user = _resolve_user_depot(request, token, db)

    doc = db.query(DepotDocument).filter(DepotDocument.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document introuvable")

    if current_user.role in ("rh", "admin"):
        pass
    else:
        emp = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
        if not emp or emp.id != doc.employe_id:
            raise HTTPException(status_code=403, detail="Accès refusé")
        if not doc.visible_employe:
            raise HTTPException(status_code=403, detail="Ce document n'est pas encore disponible")

    if not os.path.exists(doc.chemin_fichier):
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")

    return FileResponse(
        path=doc.chemin_fichier,
        filename=doc.nom_fichier,
        media_type="application/octet-stream",
    )
