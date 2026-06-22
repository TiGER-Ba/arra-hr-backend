import io
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File as FastAPIFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from sqlalchemy.orm.attributes import flag_modified

from app.config import settings
from app.database import get_db
from app.models.demande import Demande
from app.models.employee import Employe
from app.models.rh import RH
from app.models.parametrage import Parametrage
from app.models.solde import SoldeEmploye
from app.models.user import Utilisateur
from app.schemas.demande import DemandeOut, DemandeRejeter
from app.services.auth import get_password_hash, require_rh
from app.services.notifications import notifier
from app.services.pdf_generator import generate_pdf
from app.services.soldes import appliquer_deduction_sur_validation, initialiser_soldes_par_defaut

router = APIRouter()


def _get_rh(current_user: Utilisateur, db: Session) -> RH:
    rh = db.query(RH).filter(RH.utilisateur_id == current_user.id).first()
    if not rh:
        raise HTTPException(status_code=404, detail="Profil RH introuvable")
    return rh


def _enrich_demande(d: Demande) -> dict:
    out = DemandeOut.model_validate(d).model_dump()
    if d.employe and d.employe.utilisateur:
        out["nom_employe"] = d.employe.utilisateur.nom
        out["matricule"] = d.employe.matricule
    return out


# ─── Demandes ────────────────────────────────────────────────────────────────

@router.get("/demandes")
def liste_demandes(
    statut: str | None = None,
    type: str | None = None,
    search: str | None = None,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    q = db.query(Demande)
    if statut:
        q = q.filter(Demande.statut == statut)
    if type:
        q = q.filter(Demande.type == type)
    if search:
        q = q.join(Demande.employe).join(Employe.utilisateur).filter(
            Utilisateur.nom.ilike(f"%{search}%")
        )
    demandes = q.order_by(Demande.created_at.desc()).all()
    return [_enrich_demande(d) for d in demandes]


@router.get("/demandes/{demande_id}")
def detail_demande(
    demande_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    demande = db.query(Demande).filter(Demande.id == demande_id).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    return _enrich_demande(demande)


class UpdateDonneesPayload(BaseModel):
    donnees_collectees: dict


@router.get("/demandes/{demande_id}/preview")
def preview_demande(
    demande_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.services.pdf_generator import render_html
    demande = db.query(Demande).filter(Demande.id == demande_id).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    try:
        html, donnees = render_html(db, demande_id)
        # Convert all values to string for the frontend form
        donnees_str = {k: str(v) for k, v in donnees.items()}
        return {"html": html, "donnees": donnees_str}
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/demandes/{demande_id}/donnees")
def update_donnees(
    demande_id: int,
    payload: UpdateDonneesPayload,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    demande = db.query(Demande).filter(Demande.id == demande_id).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut not in ("en_attente", "en_cours"):
        raise HTTPException(status_code=400, detail="Impossible de modifier une demande déjà traitée")
    demande.donnees_collectees = payload.donnees_collectees
    flag_modified(demande, "donnees_collectees")
    db.commit()
    return {"message": "Données mises à jour"}


@router.post("/demandes/{demande_id}/valider")
def valider_demande(
    demande_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    demande = db.query(Demande).filter(Demande.id == demande_id).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut not in ("en_attente", "en_cours"):
        raise HTTPException(status_code=400, detail=f"La demande a déjà le statut : {demande.statut}")

    rh = _get_rh(current_user, db)

    try:
        document = generate_pdf(db=db, demande_id=demande_id, rh_id=rh.id)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Déduction automatique du solde correspondant
    try:
        appliquer_deduction_sur_validation(db, demande, rh_id=rh.id)
    except Exception:
        pass  # ne pas bloquer la validation si erreur de solde

    # Notifier l'employé
    if demande.employe and demande.employe.utilisateur:
        notifier(
            db=db,
            utilisateur_id=demande.employe.utilisateur.id,
            type="demande_validee",
            titre="Demande validée",
            message=f"Votre demande #{demande.id} a été validée. Le document est disponible.",
            lien=f"/employe/mes-demandes",
        )

    return {
        "message": "Demande validée et document généré avec succès",
        "document_id": document.id,
        "chemin_fichier": document.chemin_fichier,
        "demande_id": demande_id,
    }


@router.post("/demandes/{demande_id}/rejeter")
def rejeter_demande(
    demande_id: int,
    payload: DemandeRejeter,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    demande = db.query(Demande).filter(Demande.id == demande_id).first()
    if not demande:
        raise HTTPException(status_code=404, detail="Demande introuvable")
    if demande.statut not in ("en_attente", "en_cours"):
        raise HTTPException(status_code=400, detail=f"La demande a déjà le statut : {demande.statut}")

    demande.statut = "rejetee"
    demande.raison_rejet = payload.raison
    db.commit()

    # Notifier l'employé
    if demande.employe and demande.employe.utilisateur:
        notifier(
            db=db,
            utilisateur_id=demande.employe.utilisateur.id,
            type="demande_rejetee",
            titre="Demande rejetée",
            message=f"Votre demande #{demande.id} a été rejetée. Motif : {payload.raison}",
            lien=f"/employe/mes-demandes",
        )

    return {"message": "Demande rejetée", "demande_id": demande_id}


@router.get("/stats")
def stats(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    rows = db.query(Demande.type, Demande.statut, func.count(Demande.id)).group_by(Demande.type, Demande.statut).all()
    result: dict = {}
    for type_, statut, count in rows:
        result.setdefault(type_, {})[statut] = count
    totals = db.query(Demande.statut, func.count(Demande.id)).group_by(Demande.statut).all()
    return {"par_type": result, "totaux": {s: c for s, c in totals}}


@router.get("/dashboard")
def dashboard(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.models.document import Document as DocumentModel

    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    urgent_threshold = now - timedelta(days=2)

    # Pending demandes enriched
    pending = db.query(Demande).filter(Demande.statut == "en_attente").order_by(Demande.created_at.asc()).all()
    pending_data = []
    for d in pending:
        nom = d.employe.utilisateur.nom if d.employe and d.employe.utilisateur else "—"
        matricule = d.employe.matricule if d.employe else "—"
        poste = d.employe.poste if d.employe else "—"
        created = d.created_at.replace(tzinfo=timezone.utc) if d.created_at.tzinfo is None else d.created_at
        days_waiting = (now - created).days
        pending_data.append({
            "id": d.id,
            "type": d.type,
            "nom_employe": nom,
            "matricule": matricule,
            "poste": poste,
            "employe_id": d.employe_id,
            "donnees_collectees": d.donnees_collectees,
            "days_waiting": days_waiting,
            "urgent": days_waiting >= 2,
            "created_at": d.created_at.isoformat(),
        })

    # Recent activity (last 10 validated or rejected)
    recent = (
        db.query(Demande)
        .filter(Demande.statut.in_(["validee", "rejetee"]))
        .order_by(Demande.updated_at.desc())
        .limit(8)
        .all()
    )
    recent_data = []
    for d in recent:
        nom = d.employe.utilisateur.nom if d.employe and d.employe.utilisateur else "—"
        recent_data.append({
            "id": d.id,
            "type": d.type,
            "statut": d.statut,
            "nom_employe": nom,
            "updated_at": d.updated_at.isoformat(),
        })

    # Counts
    total_pending = len(pending_data)
    urgent_count = sum(1 for d in pending_data if d["urgent"])

    validated_today = db.query(func.count(Demande.id)).filter(
        Demande.statut == "validee",
        Demande.updated_at >= today_start.replace(tzinfo=None),
    ).scalar() or 0

    rejected_today = db.query(func.count(Demande.id)).filter(
        Demande.statut == "rejetee",
        Demande.updated_at >= today_start.replace(tzinfo=None),
    ).scalar() or 0

    total_employes = db.query(func.count(Employe.id)).scalar() or 0
    total_docs = db.query(func.count(DocumentModel.id)).scalar() or 0

    # Top demande types
    type_rows = db.query(Demande.type, func.count(Demande.id).label("n")).group_by(Demande.type).order_by(func.count(Demande.id).desc()).limit(4).all()
    top_types = [{"type": t, "count": n} for t, n in type_rows]

    return {
        "pending": pending_data,
        "recent_activity": recent_data,
        "stats": {
            "total_pending": total_pending,
            "urgent_count": urgent_count,
            "validated_today": validated_today,
            "rejected_today": rejected_today,
            "total_employes": total_employes,
            "total_docs": total_docs,
        },
        "top_types": top_types,
    }


# ─── Registre des congés ─────────────────────────────────────────────────────

def _normalize_date_to_iso(d: str) -> str:
    """Convert dd/mm/yyyy to yyyy-mm-dd; pass through if already ISO."""
    if not d:
        return d
    if "/" in d:
        parts = d.split("/")
        if len(parts) == 3 and len(parts[2]) == 4:
            return f"{parts[2]}-{parts[1]}-{parts[0]}"
    return d


def _build_conge_rows(db: Session) -> list[dict]:
    conge_types = ("demande_conge", "attestation_conge")
    demandes = (
        db.query(Demande)
        .filter(Demande.type.in_(conge_types))
        .order_by(Demande.created_at.desc())
        .all()
    )
    rows = []
    for d in demandes:
        emp = d.employe
        if not emp or not emp.utilisateur:
            continue
        dc = d.donnees_collectees or {}
        nom_complet = emp.utilisateur.nom.split(" ", 1)
        nom = nom_complet[0] if len(nom_complet) >= 1 else ""
        prenom = nom_complet[1] if len(nom_complet) >= 2 else ""
        type_conge = dc.get("type_conge", "Congé annuel")
        date_debut = dc.get("date_debut", "")
        date_fin = dc.get("date_fin", "")

        nb_jours = 0
        try:
            from app.services.soldes import _compter_jours
            if date_debut and date_fin:
                nb_jours = _compter_jours(
                    _normalize_date_to_iso(date_debut),
                    _normalize_date_to_iso(date_fin),
                )
        except Exception:
            nb_jours = 0

        solde_restant = None
        solde = db.query(SoldeEmploye).filter(
            SoldeEmploye.employe_id == emp.id,
            SoldeEmploye.type == "conge_annuel",
            SoldeEmploye.annee_reference == datetime.now().year,
        ).first()
        if solde:
            solde_restant = float(solde.quota_total) - float(solde.consomme)

        rows.append({
            "demande_id": d.id,
            "matricule": emp.matricule,
            "nom": nom,
            "prenom": prenom,
            "service": emp.departement,
            "date_embauche": emp.date_embauche.strftime("%d/%m/%Y"),
            "type_conge": type_conge,
            "date_debut": date_debut,
            "date_fin": date_fin,
            "nb_jours": nb_jours,
            "statut": d.statut.capitalize().replace("_", " "),
            "solde_restant": solde_restant,
        })
    return rows


@router.get("/registre-conges")
def registre_conges(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    return _build_conge_rows(db)


@router.get("/registre-conges/export")
def export_registre_conges(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    rows = _build_conge_rows(db)

    wb = Workbook()
    ws = wb.active
    ws.title = "Registre des Congés"

    headers = [
        "Matricule", "Nom", "Prénom", "Service", "Date d'embauche",
        "Type de congé", "Date début", "Date fin", "Nb jours", "Statut", "Solde restant",
    ]

    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_alignment
        cell.border = thin_border

    for row_idx, row in enumerate(rows, 2):
        values = [
            row["matricule"], row["nom"], row["prenom"], row["service"],
            row["date_embauche"], row["type_conge"], row["date_debut"],
            row["date_fin"], row["nb_jours"], row["statut"],
            row["solde_restant"] if row["solde_restant"] is not None else "—",
        ]
        for col_idx, val in enumerate(values, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center", vertical="center")

    col_widths = [12, 15, 15, 18, 16, 18, 14, 14, 10, 14, 14]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"registre_conges_{datetime.now().strftime('%Y%m%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ─── Employés CRUD ───────────────────────────────────────────────────────────

class EmployeCreate(BaseModel):
    nom: str
    email: str
    mot_de_passe: str
    matricule: str
    poste: str
    departement: str
    salaire_base: float
    date_embauche: date
    type_contrat: Optional[str] = "CDI"
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


class EmployeUpdate(BaseModel):
    nom: Optional[str] = None
    email: Optional[str] = None
    poste: Optional[str] = None
    departement: Optional[str] = None
    salaire_base: Optional[float] = None
    date_embauche: Optional[date] = None
    statut: Optional[str] = None
    type_contrat: Optional[str] = None
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


def _employe_to_dict(e: Employe) -> dict:
    return {
        "id": e.id,
        "utilisateur_id": e.utilisateur_id,
        "nom": e.utilisateur.nom,
        "email": e.utilisateur.email,
        "matricule": e.matricule,
        "poste": e.poste,
        "departement": e.departement,
        "salaire_base": float(e.salaire_base),
        "date_embauche": e.date_embauche.isoformat(),
        "statut": e.statut,
        "type_contrat": e.type_contrat,
        "cin": e.cin,
        "cnss": e.cnss,
        "adresse": e.adresse,
        "telephone": e.telephone,
    }


@router.get("/employes")
def liste_employes(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    return [_employe_to_dict(e) for e in db.query(Employe).all()]


@router.get("/employes/{employe_id}")
def get_employe(
    employe_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    e = db.query(Employe).filter(Employe.id == employe_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    return _employe_to_dict(e)


@router.post("/employes", status_code=201)
def create_employe(
    payload: EmployeCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if db.query(Utilisateur).filter(Utilisateur.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email déjà utilisé")
    if db.query(Employe).filter(Employe.matricule == payload.matricule).first():
        raise HTTPException(status_code=400, detail="Matricule déjà utilisé")

    user = Utilisateur(
        nom=payload.nom,
        email=payload.email,
        mot_de_passe=get_password_hash(payload.mot_de_passe),
        role="employe",
    )
    db.add(user)
    db.flush()

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
    db.commit()
    db.refresh(emp)
    # Initialiser les soldes par défaut
    initialiser_soldes_par_defaut(db, emp.id)
    return _employe_to_dict(emp)


@router.put("/employes/{employe_id}")
def update_employe(
    employe_id: int,
    payload: EmployeUpdate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    e = db.query(Employe).filter(Employe.id == employe_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employé introuvable")

    if payload.nom is not None:
        e.utilisateur.nom = payload.nom
    if payload.email is not None:
        existing = db.query(Utilisateur).filter(
            Utilisateur.email == payload.email,
            Utilisateur.id != e.utilisateur_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email déjà utilisé")
        e.utilisateur.email = payload.email
    if payload.poste is not None:
        e.poste = payload.poste
    if payload.departement is not None:
        e.departement = payload.departement
    if payload.salaire_base is not None:
        e.salaire_base = payload.salaire_base
    if payload.date_embauche is not None:
        e.date_embauche = payload.date_embauche
    if payload.statut is not None:
        e.statut = payload.statut
    if payload.type_contrat is not None:
        e.type_contrat = payload.type_contrat
    if payload.cin is not None:
        e.cin = payload.cin
    if payload.cnss is not None:
        e.cnss = payload.cnss
    if payload.adresse is not None:
        e.adresse = payload.adresse
    if payload.telephone is not None:
        e.telephone = payload.telephone

    db.commit()
    db.refresh(e)
    return _employe_to_dict(e)


@router.get("/employes/{employe_id}/demandes")
def get_employe_demandes(
    employe_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    e = db.query(Employe).filter(Employe.id == employe_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    demandes = db.query(Demande).filter(Demande.employe_id == employe_id).order_by(Demande.created_at.desc()).all()
    return [_enrich_demande(d) for d in demandes]


@router.delete("/employes/{employe_id}", status_code=204)
def delete_employe(
    employe_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    e = db.query(Employe).filter(Employe.id == employe_id).first()
    if not e:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    user = e.utilisateur
    db.delete(e)
    db.delete(user)
    db.commit()


# ─── Paramétrage (signature + cachet) ───────────────────────────────────────

@router.get("/parametrage")
def get_parametrage(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    sig = db.query(Parametrage).filter(Parametrage.cle == "signature").first()
    cachet = db.query(Parametrage).filter(Parametrage.cle == "cachet").first()
    return {
        "signature_url": sig.valeur if sig else None,
        "cachet_url": cachet.valeur if cachet else None,
    }


def _save_parametrage_image(db: Session, cle: str, file: UploadFile) -> str:
    import os
    upload_dir = os.path.join(settings.UPLOADS_DIR, "parametrage")
    os.makedirs(upload_dir, exist_ok=True)

    ext = file.filename.rsplit(".", 1)[-1] if "." in file.filename else "png"
    filename = f"{cle}.{ext}"
    filepath = os.path.join(upload_dir, filename)

    with open(filepath, "wb") as f:
        f.write(file.file.read())

    url_path = f"/uploads/parametrage/{filename}"

    row = db.query(Parametrage).filter(Parametrage.cle == cle).first()
    if row:
        row.valeur = url_path
    else:
        db.add(Parametrage(cle=cle, valeur=url_path))
    db.commit()
    return url_path


@router.post("/parametrage/signature")
def upload_signature(
    file: UploadFile = FastAPIFile(...),
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    url = _save_parametrage_image(db, "signature", file)
    return {"signature_url": url}


@router.post("/parametrage/cachet")
def upload_cachet(
    file: UploadFile = FastAPIFile(...),
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    url = _save_parametrage_image(db, "cachet", file)
    return {"cachet_url": url}


@router.delete("/parametrage/signature")
def delete_signature(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    row = db.query(Parametrage).filter(Parametrage.cle == "signature").first()
    if row:
        import os
        filepath = os.path.join(".", row.valeur.lstrip("/"))
        if os.path.exists(filepath):
            os.remove(filepath)
        db.delete(row)
        db.commit()
    return {"message": "Signature supprimée"}


@router.delete("/parametrage/cachet")
def delete_cachet(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    row = db.query(Parametrage).filter(Parametrage.cle == "cachet").first()
    if row:
        import os
        filepath = os.path.join(".", row.valeur.lstrip("/"))
        if os.path.exists(filepath):
            os.remove(filepath)
        db.delete(row)
        db.commit()
    return {"message": "Cachet supprimé"}
