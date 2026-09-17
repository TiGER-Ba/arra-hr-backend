import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
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
from app.services import dossier_employe, nextcloud, stockage
from app.services.profil import profil_rh
from app.services.security import read_upload_limited, safe_filename, safe_join

router = APIRouter()

DEPOT_DIR = os.path.join(settings.UPLOADS_DIR, "depot")


def _get_rh(user: Utilisateur, db: Session) -> RH:
    """Profil RH du déposant — créé au besoin (cf. services/profil)."""
    return profil_rh(user, db)


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

    # ⚠️ SÉCURITÉ : nom de fichier ET matricule sont assainis avant de composer un
    # chemin (sinon « ../ » permettrait d'écrire hors du dépôt). La taille est
    # plafonnée AVANT écriture sur disque.
    contenu = read_upload_limited(fichier, settings.MAX_UPLOAD_MB)
    safe_name = safe_filename(fichier.filename)

    cfg = nextcloud.config(db, obligatoire=False)
    if cfg:
        # ⚠️ Le SOUS-DOSSIER porte la visibilité : un document non visible part
        # dans « Administratif », que le salarié ne voit pas — y compris pour
        # un RH qui parcourt le drive directement.
        try:
            dossier = dossier_employe.assurer_dossier(cfg, emp)
            nom = dossier_employe.nom_disponible(cfg, dossier, visible_employe, safe_name)
            relatif = nextcloud.envoyer(
                cfg,
                dossier_employe.chemin_document(dossier, visible_employe, nom),
                contenu,
                fichier.content_type,
            )
        except nextcloud.NextcloudIndisponible as e:
            # Échouer AVANT d'écrire en base : une ligne sans fichier derrière
            # afficherait un document impossible à ouvrir.
            raise HTTPException(status_code=503, detail=str(e))
        dest_path = stockage.chemin_distant(relatif)
    else:
        emp_dir = safe_join(DEPOT_DIR, f"employe_{safe_filename(emp.matricule, 'inconnu')}")
        os.makedirs(emp_dir, exist_ok=True)

        base, ext = os.path.splitext(safe_name)
        counter = 1
        dest_path = safe_join(emp_dir, safe_name)
        while os.path.exists(dest_path):
            dest_path = safe_join(emp_dir, f"{base}_{counter}{ext}")
            counter += 1

        with open(dest_path, "wb") as f:
            f.write(contenu)

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
    if visible_employe is not None and visible_employe != doc.visible_employe:
        # ⚠️ Sur Nextcloud, c'est le SOUS-DOSSIER qui dit la visibilité. Changer
        # la colonne sans déplacer le fichier laisserait un document
        # « administratif » dans « Partagé » : le RH qui parcourt le drive y
        # lirait le contraire de la vérité, et la synchronisation descendante
        # rebasculerait la visibilité au passage suivant.
        if stockage.est_distant(doc.chemin_fichier):
            cfg = nextcloud.config(db)
            ancien = stockage.sans_prefixe(doc.chemin_fichier)
            dossier = dossier_employe.assurer_dossier(cfg, doc.employe)
            nom = dossier_employe.nom_disponible(
                cfg, dossier, visible_employe, ancien.split("/")[-1])
            nouveau = dossier_employe.chemin_document(dossier, visible_employe, nom)
            try:
                nextcloud.deplacer(cfg, ancien, nouveau)
            except nextcloud.NextcloudIndisponible as e:
                # Ne pas changer la colonne si le fichier n'a pas bougé
                raise HTTPException(status_code=503, detail=str(e))
            doc.chemin_fichier = stockage.chemin_distant(nouveau)
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
    # ⚠️ Retirer le fichier AUSSI : laissé sur Nextcloud, la synchronisation
    # descendante le réimporterait et la suppression semblerait sans effet.
    try:
        stockage.supprimer(db, doc.chemin_fichier)
    except nextcloud.NextcloudIndisponible as e:
        raise HTTPException(status_code=503, detail=str(e))
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
    inline: bool = Query(default=False),  # True = visualisation dans le navigateur
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

    # ⚠️ Le fichier peut être sur Nextcloud ou sur le disque (dépôts antérieurs).
    # `stockage.lire` tranche d'après le chemin ; l'autorisation, elle, vient
    # d'être vérifiée juste au-dessus — ce module ne décide de rien.
    try:
        contenu = stockage.lire(db, doc.chemin_fichier)
    except stockage.FichierIntrouvable:
        raise HTTPException(status_code=404, detail="Fichier introuvable sur le serveur")
    except nextcloud.NonConfigure:
        raise HTTPException(
            status_code=503,
            detail="Ce document est stocké sur Nextcloud, qui n'est plus configuré.",
        )
    except nextcloud.NextcloudIndisponible as e:
        # 503 et non 404 : le document existe, c'est le stockage qui répond mal.
        # Les confondre enverrait chercher un fichier qui est bien là.
        raise HTTPException(status_code=503, detail=str(e))

    import mimetypes
    media = (mimetypes.guess_type(doc.nom_fichier)[0]
             or ("application/pdf" if inline else "application/octet-stream"))
    disposition = "inline" if inline else "attachment"
    nom = safe_filename(doc.nom_fichier) or "document"
    return Response(
        content=contenu,
        media_type=media,
        headers={"Content-Disposition": f'{disposition}; filename="{nom}"'},
    )
