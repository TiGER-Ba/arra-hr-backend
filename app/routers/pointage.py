"""Pointage / feuille de temps.

- Employé : saisit ses absences du mois (jour ouvré sans entrée = travaillé),
  soumet sa feuille.
- RH : voit le récapitulatif mensuel (1 ligne par employé, façon « Suivi de la
  paie ») et exporte le mois sélectionné en .xlsx.
"""
import calendar
import io
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.employee import Employe
from app.models.pointage import ABSENCE_TYPES, FeuilleTemps, Pointage
from app.models.solde import SoldeEmploye
from app.models.user import Utilisateur
from app.services.auth import get_current_user, require_rh

router = APIRouter()

JOURS_FR = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]
MOIS_ABBR = ["", "Jan", "Fév", "Mar", "Avr", "Mai", "Juin", "Juil", "Août", "Sep", "Oct", "Nov", "Déc"]


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _valider_periode(annee: int, mois: int) -> None:
    if not (1 <= mois <= 12) or not (2000 <= annee <= 2100):
        raise HTTPException(status_code=400, detail="Période invalide")


def _mois_bornes(annee: int, mois: int):
    nb = calendar.monthrange(annee, mois)[1]
    return date(annee, mois, 1), date(annee, mois, nb), nb


def _weekdays(annee: int, mois: int) -> list[date]:
    _, _, nb = _mois_bornes(annee, mois)
    return [date(annee, mois, d) for d in range(1, nb + 1) if date(annee, mois, d).weekday() < 5]


def _get_employe(current_user: Utilisateur, db: Session) -> Employe:
    emp = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Profil employé introuvable")
    return emp


def _compter(entries_types: dict, weekdays: list[date], feries: dict | None = None) -> dict:
    """entries_types = {iso_date: type}. Ne compte que les jours ouvrés.

    `feries` = {iso: libellé} : les fériés officiels comptent comme non travaillés
    même s'ils n'ont pas encore été enregistrés par l'employé.
    """
    wd = {d.isoformat() for d in weekdays}
    if feries:
        # Un féri officiel prime, sauf si l'employé a explicitement saisi autre chose
        entries_types = {**{iso: "ferie" for iso in feries if iso in wd}, **entries_types}
    cp = ss = mal = fe = 0
    for iso, t in entries_types.items():
        if iso not in wd:
            continue
        if t == "conge_paye":
            cp += 1
        elif t == "conge_sans_solde":
            ss += 1
        elif t == "maladie":
            mal += 1
        elif t == "ferie":
            fe += 1
    production = len(weekdays) - cp - ss - mal - fe
    return {
        "jours_ouvres": len(weekdays), "production": production,
        "conge_paye": cp, "conge_sans_solde": ss, "maladie": mal, "ferie": fe,
    }


def _entries(db: Session, employe_id: int, annee: int, mois: int) -> dict:
    debut, fin, _ = _mois_bornes(annee, mois)
    rows = db.query(Pointage).filter(
        Pointage.employe_id == employe_id,
        Pointage.date_jour >= debut, Pointage.date_jour <= fin,
    ).all()
    return {p.date_jour.isoformat(): p for p in rows}


# ─── Employé ─────────────────────────────────────────────────────────────────

@router.get("/ma-feuille")
def ma_feuille(
    annee: int, mois: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(annee, mois)
    emp = _get_employe(current_user, db)
    _, _, nb = _mois_bornes(annee, mois)
    entries = _entries(db, emp.id, annee, mois)

    # Fériés officiels : pré-remplis automatiquement (l'employé n'a plus à les saisir)
    from app.services.feries import feries_du_mois
    feries = feries_du_mois(db, annee, mois)

    jours = []
    for d in range(1, nb + 1):
        jd = date(annee, mois, d)
        iso = jd.isoformat()
        we = jd.weekday() >= 5
        p = entries.get(iso)
        ferie_libelle = feries.get(iso)
        if p:
            type_jour = p.type
        elif we:
            type_jour = None
        elif ferie_libelle:
            type_jour = "ferie"
        else:
            type_jour = "travaille"
        jours.append({
            "date": iso,
            "jour": d,
            "jour_semaine": JOURS_FR[jd.weekday()],
            "weekend": we,
            "type": type_jour,
            "commentaire": p.commentaire if p else None,
            "ferie": ferie_libelle,  # libellé officiel si le jour est férié
        })

    feuille = db.query(FeuilleTemps).filter_by(employe_id=emp.id, annee=annee, mois=mois).first()
    entries_types = {k: v.type for k, v in entries.items()}
    return {
        "annee": annee, "mois": mois,
        "statut": feuille.statut if feuille else "brouillon",
        "jours": jours,
        "resume": _compter(entries_types, _weekdays(annee, mois), feries),
    }


class JourEntry(BaseModel):
    date: str
    type: str
    commentaire: str | None = None


class FeuilleSave(BaseModel):
    annee: int
    mois: int
    entrees: list[JourEntry] = []


class PeriodeBody(BaseModel):
    annee: int
    mois: int


@router.put("/ma-feuille")
def enregistrer_feuille(
    payload: FeuilleSave,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(payload.annee, payload.mois)
    emp = _get_employe(current_user, db)
    feuille = db.query(FeuilleTemps).filter_by(employe_id=emp.id, annee=payload.annee, mois=payload.mois).first()
    if feuille and feuille.statut == "soumise":
        raise HTTPException(status_code=400, detail="Feuille déjà soumise. Rouvrez-la pour la modifier.")

    debut, fin, _ = _mois_bornes(payload.annee, payload.mois)
    valides: dict[str, tuple] = {}
    for e in payload.entrees:
        try:
            jd = date.fromisoformat(e.date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Date invalide : {e.date}")
        if jd < debut or jd > fin:
            raise HTTPException(status_code=400, detail="Date hors du mois")
        if jd.weekday() >= 5 or e.type == "travaille":
            continue  # week-end ou jour travaillé → non stocké
        if e.type not in ABSENCE_TYPES:
            raise HTTPException(status_code=400, detail=f"Type invalide : {e.type}")
        valides[jd.isoformat()] = (jd, e.type, (e.commentaire or None))

    # Remplace intégralement le mois
    db.query(Pointage).filter(
        Pointage.employe_id == emp.id,
        Pointage.date_jour >= debut, Pointage.date_jour <= fin,
    ).delete(synchronize_session=False)
    for jd, t, com in valides.values():
        db.add(Pointage(employe_id=emp.id, date_jour=jd, type=t, commentaire=com))
    if not feuille:
        feuille = FeuilleTemps(employe_id=emp.id, annee=payload.annee, mois=payload.mois, statut="brouillon")
        db.add(feuille)
    db.commit()

    entries_types = {k: v[1] for k, v in valides.items()}
    return {
        "message": "Feuille enregistrée",
        "statut": feuille.statut,
        "resume": _compter(entries_types, _weekdays(payload.annee, payload.mois)),
    }


@router.post("/ma-feuille/soumettre")
def soumettre_feuille(
    payload: PeriodeBody,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(payload.annee, payload.mois)
    emp = _get_employe(current_user, db)
    feuille = db.query(FeuilleTemps).filter_by(employe_id=emp.id, annee=payload.annee, mois=payload.mois).first()
    if not feuille:
        feuille = FeuilleTemps(employe_id=emp.id, annee=payload.annee, mois=payload.mois)
        db.add(feuille)
    feuille.statut = "soumise"
    db.commit()
    return {"message": "Feuille soumise", "statut": "soumise"}


@router.post("/ma-feuille/rouvrir")
def rouvrir_feuille(
    payload: PeriodeBody,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(payload.annee, payload.mois)
    emp = _get_employe(current_user, db)
    feuille = db.query(FeuilleTemps).filter_by(employe_id=emp.id, annee=payload.annee, mois=payload.mois).first()
    if feuille:
        feuille.statut = "brouillon"
        db.commit()
    return {"message": "Feuille rouverte", "statut": "brouillon"}


# ─── RH : récapitulatif + export ─────────────────────────────────────────────

def _rh_rows(db: Session, annee: int, mois: int) -> list[dict]:
    debut, fin, _ = _mois_bornes(annee, mois)
    weekdays = _weekdays(annee, mois)
    wd = {d.isoformat() for d in weekdays}

    pts = db.query(Pointage).filter(
        Pointage.date_jour >= debut, Pointage.date_jour <= fin
    ).all()
    by_emp: dict[int, dict] = {}
    for p in pts:
        if p.date_jour.isoformat() in wd:
            by_emp.setdefault(p.employe_id, {})
            by_emp[p.employe_id][p.type] = by_emp[p.employe_id].get(p.type, 0) + 1

    feuilles = {f.employe_id: f.statut for f in db.query(FeuilleTemps).filter_by(annee=annee, mois=mois).all()}

    # Fériés officiels du mois : non travaillés pour tout le monde, même si
    # l'employé ne les a pas saisis (ils ne sont pas décomptés des congés).
    from app.services.feries import feries_du_mois
    feries_iso = set(feries_du_mois(db, annee, mois).keys()) & wd

    rows = []
    for emp in db.query(Employe).all():
        cnt = by_emp.get(emp.id, {})
        cp = cnt.get("conge_paye", 0)
        ss = cnt.get("conge_sans_solde", 0)
        mal = cnt.get("maladie", 0)
        # Fériés saisis + fériés officiels non saisis (sans double comptage)
        saisis = {p.date_jour.isoformat() for p in pts if p.employe_id == emp.id}
        fe = cnt.get("ferie", 0) + len(feries_iso - saisis)
        production = len(weekdays) - cp - ss - mal - fe

        solde = db.query(SoldeEmploye).filter_by(
            employe_id=emp.id, type="conge_annuel", annee_reference=annee
        ).first()
        solde_reste = (float(solde.quota_total) - float(solde.consomme)) if solde else None

        u = emp.utilisateur
        notes = []
        if mal:
            notes.append(f"{mal} j maladie")
        if fe:
            notes.append(f"{fe} j férié")

        rows.append({
            "employe_id": emp.id,
            "type": "Salarié",
            "nom": u.nom if u else "",
            "prenom": u.prenom if u else "",
            "sal_net": float(emp.salaire_base),
            "production": production,
            "conges_payes": cp,
            "conges_sans_solde": ss,
            "maladie": mal,
            "ferie": fe,
            "solde_conges": solde_reste,
            "statut": feuilles.get(emp.id, "non_rempli"),
            "commentaire": " · ".join(notes),
        })
    rows.sort(key=lambda r: ((r["nom"] or "").lower(), (r["prenom"] or "").lower()))
    return rows


@router.get("/recap")
def recap(
    annee: int, mois: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    _valider_periode(annee, mois)
    return {
        "annee": annee, "mois": mois,
        "jours_ouvres": len(_weekdays(annee, mois)),
        "lignes": _rh_rows(db, annee, mois),
    }


# ─── CRA : feuille des temps en PDF ──────────────────────────────────────────

def _reponse_cra(db: Session, emp: Employe, annee: int, mois: int, inline: bool):
    from app.services.cra import generer_pdf_cra, nom_fichier_cra

    try:
        pdf = generer_pdf_cra(db, emp, annee, mois)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    nom = nom_fichier_cra(emp, annee, mois)
    disposition = "inline" if inline else "attachment"
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disposition}; filename="{nom}"'},
    )


@router.get("/cra")
def cra_employe(
    annee: int, mois: int, employe_id: int,
    inline: bool = False,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """CRA d'un salarié (RH/admin) — feuille des temps détaillée du mois."""
    _valider_periode(annee, mois)
    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    return _reponse_cra(db, emp, annee, mois, inline)


@router.get("/ma-cra")
def ma_cra(
    annee: int, mois: int,
    inline: bool = False,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Le salarié télécharge son propre CRA."""
    _valider_periode(annee, mois)
    return _reponse_cra(db, _get_employe(current_user, db), annee, mois, inline)


@router.get("/cra-tous")
def cra_tous(
    annee: int, mois: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Archive ZIP des CRA de tous les salariés du mois (un PDF par salarié)."""
    import zipfile

    from app.services.cra import generer_pdf_cra, nom_fichier_cra

    _valider_periode(annee, mois)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        for emp in db.query(Employe).all():
            try:
                archive.writestr(nom_fichier_cra(emp, annee, mois), generer_pdf_cra(db, emp, annee, mois))
            except Exception as e:  # noqa: BLE001
                # Un salarié en erreur ne doit pas faire échouer toute l'archive
                print(f"[cra] échec pour l'employé {emp.id} : {e}")
    tampon.seek(0)
    return StreamingResponse(
        tampon,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="CRA_{annee}_{mois:02d}.zip"'},
    )


# ─── Jours fériés (RH) ───────────────────────────────────────────────────────

class FerieCreate(BaseModel):
    date_jour: str
    libelle: str


@router.get("/feries")
def liste_feries(
    annee: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Fériés de l'année. Les fériés fixes sont créés automatiquement au besoin."""
    from app.models.pointage import JourFerie
    from app.services.feries import FERIES_RELIGIEUX_LABELS, assurer_feries_fixes

    _valider_periode(annee, 1)
    assurer_feries_fixes(db, annee)
    lignes = db.query(JourFerie).filter(JourFerie.annee == annee).order_by(JourFerie.date_jour).all()
    return {
        "annee": annee,
        "feries": [
            {"id": f.id, "date": f.date_jour.isoformat(), "libelle": f.libelle, "fixe": f.fixe}
            for f in lignes
        ],
        # Fêtes religieuses : dates variables (calendrier hégirien) → saisie manuelle
        "suggestions_religieuses": FERIES_RELIGIEUX_LABELS,
    }


@router.post("/feries", status_code=201)
def creer_ferie(
    payload: FerieCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.models.pointage import JourFerie
    try:
        jd = date.fromisoformat(payload.date_jour)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date invalide")
    if not payload.libelle.strip():
        raise HTTPException(status_code=400, detail="Le libellé est requis")
    if db.query(JourFerie).filter(JourFerie.date_jour == jd).first():
        raise HTTPException(status_code=400, detail="Ce jour est déjà déclaré férié")
    f = JourFerie(date_jour=jd, libelle=payload.libelle.strip(), annee=jd.year, fixe=False)
    db.add(f)
    db.commit()
    db.refresh(f)
    return {"id": f.id, "date": f.date_jour.isoformat(), "libelle": f.libelle, "fixe": f.fixe}


@router.delete("/feries/{ferie_id}", status_code=204)
def supprimer_ferie(
    ferie_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.models.pointage import JourFerie
    f = db.query(JourFerie).filter(JourFerie.id == ferie_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Jour férié introuvable")
    db.delete(f)
    db.commit()


@router.get("/export")
def export_paie(
    annee: int, mois: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    _valider_periode(annee, mois)
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    rows = _rh_rows(db, annee, mois)
    wb = Workbook()
    ws = wb.active
    ws.title = f"{MOIS_ABBR[mois]}-{str(annee)[2:]}"

    headers = [
        "Type", "Ressource - Nom", "Ressource - Prénom", "Sal Net (dont la prime)",
        "Prime", "Production", "Congés Payés", "Congés Sans solde", "Prime AID",
        "Commentaires", "Solde congés",
    ]
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)
    thin = Border(*(Side(style="thin"),) * 4)

    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
        cell.border = thin

    for i, r in enumerate(rows, 2):
        sal = f"{r['sal_net']:,.2f} MAD".replace(",", " ")
        values = [
            r["type"], r["nom"], r["prenom"], sal, "",
            r["production"], r["conges_payes"], r["conges_sans_solde"], "",
            r["commentaire"], r["solde_conges"] if r["solde_conges"] is not None else "",
        ]
        for c, v in enumerate(values, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.border = thin
            cell.alignment = Alignment(horizontal="center", vertical="center")

    widths = [12, 16, 16, 20, 12, 12, 12, 14, 10, 30, 12]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"suivi_paie_{annee}_{mois:02d}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
