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


def _saisies(db: Session, employe_id: int, annee: int, mois: int) -> list[Pointage]:
    debut, fin, _ = _mois_bornes(annee, mois)
    return db.query(Pointage).filter(
        Pointage.employe_id == employe_id,
        Pointage.date_jour >= debut, Pointage.date_jour <= fin,
    ).all()


def _cle_ligne(p: Pointage) -> tuple:
    """Identifie la ligne de la grille à laquelle appartient une saisie."""
    return (p.categorie or "absence", p.projet_id, p.type)


def _est_exceptionnel(jour: date, feries: dict) -> bool:
    """Jour de repos : samedi, dimanche ou jour férié.

    Y travailler ouvre droit à majoration (Code du travail marocain) : ces jours
    doivent donc être identifiables séparément jusque dans l'export de paie.
    """
    return jour.weekday() >= 5 or jour.isoformat() in feries


def _jours_attendus(weekdays: list[date], feries: dict, override: float | None) -> float:
    """Nombre de jours que le salarié doit déclarer.

    Par défaut : jours ouvrés moins les jours fériés tombant en semaine.
    L'administrateur peut imposer une autre valeur (temps partiel, arrivée ou
    départ en cours de mois, fermeture d'entreprise).
    """
    if override is not None:
        return float(override)
    ouvres = {d.isoformat() for d in weekdays}
    feries_ouvres = len([iso for iso in feries if iso in ouvres])
    return float(len(weekdays) - feries_ouvres)


def _totaux(saisies: list[Pointage], weekdays: list[date], feries: dict,
            attendu_override: float | None = None) -> dict:
    """Totaux du mois, en JOURS (une saisie vaut 0,5 ou 1).

    Les fériés officiels non saisis sont ajoutés : ils ne sont ni travaillés ni
    décomptés des congés. Le temps déclaré un jour de repos est compté dans la
    production ET isolé dans `exceptionnel`.
    """
    ouvres = {d.isoformat() for d in weekdays}
    saisis = {p.date_jour.isoformat() for p in saisies}

    par_categorie = {"production": 0.0, "absence": 0.0, "interne": 0.0}
    par_type: dict[str, float] = {}
    exceptionnel = 0.0

    for p in saisies:
        val = float(p.valeur or 1)
        categorie = p.categorie or "absence"
        par_categorie[categorie] = par_categorie.get(categorie, 0.0) + val
        par_type[p.type] = par_type.get(p.type, 0.0) + val
        # Travail (production ou interne) un jour de repos → majorable
        if categorie != "absence" and _est_exceptionnel(p.date_jour, feries):
            exceptionnel += val

    # Fériés officiels qu'aucune saisie ne recouvre : chômés
    feries_implicites = len([iso for iso in feries if iso in ouvres and iso not in saisis])
    if feries_implicites:
        par_categorie["absence"] += feries_implicites
        par_type["ferie"] = par_type.get("ferie", 0.0) + feries_implicites

    attendu = _jours_attendus(weekdays, feries, attendu_override)
    realise = par_categorie["production"] + par_categorie["absence"] + par_categorie["interne"]
    return {
        "jours_ouvres": float(len(weekdays)),
        # Ce que le salarié doit déclarer (fériés déduits, ou valeur imposée)
        "attendu": attendu,
        "attendu_impose": attendu_override is not None,
        "production": par_categorie["production"],
        "absence": par_categorie["absence"],
        "interne": par_categorie["interne"],
        "realise": realise,
        # Jours travaillés hors jours ouvrés — à majorer en paie
        "exceptionnel": exceptionnel,
        # Jauge de complétion. Elle n'est PAS bornée : au-dessus de 100 % en cas
        # de travail le week-end, en dessous en cas de départ en cours de mois.
        # Ces deux situations sont légitimes et ne bloquent jamais la soumission.
        "completion": round((realise / attendu) * 100) if attendu else 0,
        "ecart": round(realise - attendu, 2),
        "par_type": par_type,
        # Compteurs conservés pour l'export paie et le CRA
        "conge_paye": par_type.get("conge_paye", 0.0),
        "conge_sans_solde": par_type.get("conge_sans_solde", 0.0),
        "maladie": par_type.get("maladie", 0.0),
        "ferie": par_type.get("ferie", 0.0),
    }


# ─── Employé ─────────────────────────────────────────────────────────────────

def _construire_feuille(db: Session, emp: Employe, annee: int, mois: int) -> dict:
    """Grille mensuelle façon Boond : des LIGNES (projet ou absence) × des JOURS."""
    from app.models.crm import Affectation, Projet
    from app.models.pointage import LIBELLES_ABSENCE, LIBELLES_INTERNE
    from app.services.feries import feries_du_mois, pays_employe

    _, _, nb = _mois_bornes(annee, mois)
    # Le calendrier dépend de l'entité de rattachement (ARRA Maroc / France)
    feries = feries_du_mois(db, annee, mois, pays_employe(emp))

    jours = []
    for d in range(1, nb + 1):
        jd = date(annee, mois, d)
        iso = jd.isoformat()
        jours.append({
            "date": iso,
            "jour": d,
            "jour_semaine": JOURS_FR[jd.weekday()],
            "semaine": jd.isocalendar()[1],   # numéro de semaine (S31, S32…)
            "weekend": jd.weekday() >= 5,
            "ferie": feries.get(iso),
        })

    saisies = _saisies(db, emp.id, annee, mois)

    # Regroupement des saisies en lignes de grille
    groupes: dict[tuple, dict] = {}
    for p in saisies:
        cle = _cle_ligne(p)
        if cle not in groupes:
            categorie, projet_id, type_ = cle
            if categorie == "production" and projet_id:
                projet = db.query(Projet).filter(Projet.id == projet_id).first()
                libelle = projet.libelle if projet else f"Projet #{projet_id}"
            elif categorie == "interne":
                libelle = LIBELLES_INTERNE.get(type_, type_.capitalize())
            else:
                libelle = LIBELLES_ABSENCE.get(type_, type_.capitalize())
            groupes[cle] = {
                "categorie": categorie, "projet_id": projet_id, "type": type_,
                "libelle": libelle, "jours": {}, "commentaires": {}, "total": 0.0,
            }
        val = float(p.valeur or 1)
        groupes[cle]["jours"][p.date_jour.day] = val
        groupes[cle]["total"] += val
        if p.commentaire:
            groupes[cle]["commentaires"][p.date_jour.day] = p.commentaire

    # Ligne « Férié » implicite : les fériés officiels non saisis
    ouvres = {d.isoformat() for d in _weekdays(annee, mois)}
    saisis = {p.date_jour.isoformat() for p in saisies}
    implicites = {
        date.fromisoformat(iso).day: 1.0
        for iso in feries if iso in ouvres and iso not in saisis
    }
    if implicites:
        cle = ("absence", None, "ferie")
        if cle in groupes:
            groupes[cle]["jours"].update(implicites)
            groupes[cle]["total"] += len(implicites)
        else:
            groupes[cle] = {
                "categorie": "absence", "projet_id": None, "type": "ferie",
                "libelle": "Férié", "jours": implicites, "commentaires": {},
                "total": float(len(implicites)), "automatique": True,
            }

    ordre = {"production": 0, "interne": 1, "absence": 2}
    lignes = sorted(groupes.values(), key=lambda l: (ordre.get(l["categorie"], 9), l["libelle"]))

    # Projets sur lesquels ce salarié peut pointer
    affectations = db.query(Affectation).filter(
        Affectation.employe_id == emp.id, Affectation.actif == True,  # noqa: E712
    ).all()
    projets_dispo = []
    for a in affectations:
        p = db.query(Projet).filter(Projet.id == a.projet_id).first()
        if p and p.statut != "archive":
            projets_dispo.append({"id": p.id, "libelle": p.libelle, "reference": p.reference})

    feuille = db.query(FeuilleTemps).filter_by(employe_id=emp.id, annee=annee, mois=mois).first()
    return {
        "annee": annee, "mois": mois,
        "employe": {
            "id": emp.id, "matricule": emp.matricule,
            "nom": (
                f"{(emp.utilisateur.prenom + ' ') if emp.utilisateur and emp.utilisateur.prenom else ''}"
                f"{emp.utilisateur.nom if emp.utilisateur else ''}"
            ).strip(),
        },
        "statut": feuille.statut if feuille else "brouillon",
        "motif_rejet": feuille.motif_rejet if feuille else None,
        "commentaire": feuille.commentaire if feuille else None,
        "jours": jours,
        "lignes": lignes,
        "projets_disponibles": projets_dispo,
        "resume": _totaux(
            saisies, _weekdays(annee, mois), feries,
            float(feuille.jours_attendus) if feuille and feuille.jours_attendus is not None else None,
        ),
        # Détail des jours de repos travaillés, à justifier et à majorer
        "jours_exceptionnels": sorted(
            [
                {
                    "date": p.date_jour.isoformat(),
                    "jour": p.date_jour.day,
                    "valeur": float(p.valeur or 1),
                    "motif": "Jour férié" if p.date_jour.isoformat() in feries else "Week-end",
                    "commentaire": p.commentaire,
                }
                for p in saisies
                if (p.categorie or "absence") != "absence" and _est_exceptionnel(p.date_jour, feries)
            ],
            key=lambda x: x["jour"],
        ),
    }


@router.get("/ma-feuille")
def ma_feuille(
    annee: int, mois: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(annee, mois)
    return _construire_feuille(db, _get_employe(current_user, db), annee, mois)


@router.get("/feuille/{employe_id}")
def feuille_employe(
    employe_id: int, annee: int, mois: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """La même grille, consultée par le RH pour contrôler avant validation."""
    _valider_periode(annee, mois)
    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    return _construire_feuille(db, emp, annee, mois)


class JourEntry(BaseModel):
    date: str
    type: str
    commentaire: str | None = None


class LigneSaisie(BaseModel):
    """Une ligne de la grille : un projet OU un type d'absence, et ses jours."""
    categorie: str                 # production | absence | interne
    type: str                      # « normale », ou type d'absence/interne
    projet_id: int | None = None   # requis si categorie == production
    jours: dict[str, float] = {}   # { "12": 1, "13": 0.5 }
    # Justification par jour — attendue sur les jours de repos travaillés
    commentaires: dict[str, str] = {}


class FeuilleSave(BaseModel):
    annee: int
    mois: int
    lignes: list[LigneSaisie] = []
    commentaire: str | None = None   # mot du salarié au service RH


class PeriodeBody(BaseModel):
    annee: int
    mois: int


class RejetBody(BaseModel):
    annee: int
    mois: int
    employe_id: int
    motif: str


@router.put("/ma-feuille")
def enregistrer_feuille(
    payload: FeuilleSave,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from app.models.crm import Affectation
    from app.models.pointage import CATEGORIES

    _valider_periode(payload.annee, payload.mois)
    emp = _get_employe(current_user, db)
    feuille = db.query(FeuilleTemps).filter_by(
        employe_id=emp.id, annee=payload.annee, mois=payload.mois
    ).first()
    if feuille and feuille.statut in ("soumise", "validee"):
        raise HTTPException(
            status_code=400,
            detail="Feuille déjà soumise ou validée. Rouvrez-la pour la modifier.",
        )

    debut, fin, nb = _mois_bornes(payload.annee, payload.mois)
    projets_autorises = {
        a.projet_id for a in db.query(Affectation).filter(
            Affectation.employe_id == emp.id, Affectation.actif == True,  # noqa: E712
        ).all()
    }
    from app.services.feries import feries_du_mois, pays_employe
    feries = feries_du_mois(db, payload.annee, payload.mois, pays_employe(emp))

    nouvelles: list[Pointage] = []
    total_par_jour: dict[int, float] = {}

    for ligne in payload.lignes:
        if ligne.categorie not in CATEGORIES:
            raise HTTPException(status_code=400, detail=f"Catégorie invalide : {ligne.categorie}")

        # ⚠️ Les jours fériés relèvent de l'administrateur seul. Le salarié ne
        # peut ni les créer, ni les supprimer, ni en modifier la durée : la
        # ligne « Férié » de sa grille est calculée, pas saisie.
        # S'il a travaillé un jour férié, il renseigne sa ligne de PROJET —
        # le jour est alors marqué exceptionnel et remonte au RH.
        if ligne.type == "ferie":
            raise HTTPException(
                status_code=403,
                detail="Les jours fériés sont gérés par l'administrateur. "
                       "Si vous avez travaillé ce jour-là, déclarez-le sur la ligne du projet.",
            )

        if ligne.categorie == "production":
            if not ligne.projet_id:
                raise HTTPException(status_code=400, detail="Un projet est requis pour une ligne de production")
            # ⚠️ On ne se fie pas au client : le salarié ne peut pointer que sur
            # les projets auxquels il est réellement affecté.
            if ligne.projet_id not in projets_autorises:
                raise HTTPException(
                    status_code=403,
                    detail="Vous n'êtes pas affecté à ce projet",
                )

        for jour_str, valeur in ligne.jours.items():
            try:
                jour = int(jour_str)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail=f"Jour invalide : {jour_str}")
            if not (1 <= jour <= nb):
                raise HTTPException(status_code=400, detail=f"Jour hors du mois : {jour}")
            val = float(valeur or 0)
            if val <= 0:
                continue
            if val not in (0.5, 1.0):
                raise HTTPException(status_code=400, detail="Seules les valeurs 0,5 et 1 sont acceptées")

            jd = date(payload.annee, payload.mois, jour)
            exceptionnel = _est_exceptionnel(jd, feries)

            # Une absence un jour de repos n'a pas de sens : on ne pose pas de
            # congé un dimanche, il n'était pas travaillé.
            if exceptionnel and ligne.categorie == "absence":
                raise HTTPException(
                    status_code=400,
                    detail=f"Le {jour}/{payload.mois:02d} est un jour de repos : "
                           f"une absence ne peut pas y être déclarée.",
                )

            total_par_jour[jour] = total_par_jour.get(jour, 0.0) + val
            nouvelles.append(Pointage(
                employe_id=emp.id, date_jour=jd,
                categorie=ligne.categorie, type=ligne.type,
                projet_id=ligne.projet_id if ligne.categorie == "production" else None,
                valeur=val,
                exceptionnel=exceptionnel,
                commentaire=(ligne.commentaires.get(jour_str) or "").strip()[:255] or None,
            ))

    # Cohérence : on ne peut pas déclarer plus d'une journée sur une même date
    trop = [j for j, total in total_par_jour.items() if total > 1.0]
    if trop:
        raise HTTPException(
            status_code=400,
            detail=f"Plus d'une journée déclarée le(s) jour(s) : {', '.join(map(str, sorted(trop)))}",
        )

    # Remplacement intégral du mois
    db.query(Pointage).filter(
        Pointage.employe_id == emp.id,
        Pointage.date_jour >= debut, Pointage.date_jour <= fin,
    ).delete(synchronize_session=False)
    for p in nouvelles:
        db.add(p)
    if not feuille:
        feuille = FeuilleTemps(
            employe_id=emp.id, annee=payload.annee, mois=payload.mois, statut="brouillon"
        )
        db.add(feuille)
    feuille.motif_rejet = None
    if payload.commentaire is not None:
        feuille.commentaire = payload.commentaire.strip() or None
    db.commit()

    return {"message": "Feuille enregistrée", "statut": feuille.statut}


@router.post("/ma-feuille/soumettre")
def soumettre_feuille(
    payload: PeriodeBody,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(payload.annee, payload.mois)
    emp = _get_employe(current_user, db)
    feuille = db.query(FeuilleTemps).filter_by(
        employe_id=emp.id, annee=payload.annee, mois=payload.mois
    ).first()
    if not feuille:
        feuille = FeuilleTemps(employe_id=emp.id, annee=payload.annee, mois=payload.mois)
        db.add(feuille)
    feuille.statut = "soumise"
    feuille.motif_rejet = None
    db.commit()

    # Prévenir le service RH qu'une feuille attend une validation
    try:
        from app.services.notifications import notifier_rh
        notifier_rh(
            db=db, type="feuille_soumise", titre="Feuille de temps à valider",
            message=f"{emp.utilisateur.nom if emp.utilisateur else 'Un salarié'} a soumis sa feuille "
                    f"de {payload.mois:02d}/{payload.annee}.",
            lien="/rh/pointage",
        )
    except Exception:
        pass
    return {"message": "Feuille soumise", "statut": "soumise"}


@router.post("/ma-feuille/rouvrir")
def rouvrir_feuille(
    payload: PeriodeBody,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _valider_periode(payload.annee, payload.mois)
    emp = _get_employe(current_user, db)
    feuille = db.query(FeuilleTemps).filter_by(
        employe_id=emp.id, annee=payload.annee, mois=payload.mois
    ).first()
    if feuille:
        if feuille.statut == "validee":
            raise HTTPException(
                status_code=400,
                detail="Cette feuille a été validée par le service RH : elle n'est plus modifiable.",
            )
        feuille.statut = "brouillon"
        db.commit()
    return {"message": "Feuille rouverte", "statut": "brouillon"}


# ─── Validation par le RH ────────────────────────────────────────────────────

def _feuille_ou_404(db: Session, employe_id: int, annee: int, mois: int) -> FeuilleTemps:
    feuille = db.query(FeuilleTemps).filter_by(
        employe_id=employe_id, annee=annee, mois=mois
    ).first()
    if not feuille:
        raise HTTPException(status_code=404, detail="Aucune feuille pour cette période")
    return feuille


@router.post("/valider")
def valider_feuille(
    payload: RejetBody | PeriodeBody | dict,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Valide la feuille d'un salarié (RH/admin). Elle devient non modifiable."""
    from datetime import datetime as _dt

    annee = int(payload.get("annee")) if isinstance(payload, dict) else payload.annee
    mois = int(payload.get("mois")) if isinstance(payload, dict) else payload.mois
    employe_id = int(payload.get("employe_id")) if isinstance(payload, dict) else getattr(payload, "employe_id", None)
    if not employe_id:
        raise HTTPException(status_code=400, detail="employe_id requis")
    _valider_periode(annee, mois)

    feuille = _feuille_ou_404(db, employe_id, annee, mois)
    if feuille.statut != "soumise":
        raise HTTPException(
            status_code=400,
            detail=f"Seule une feuille soumise peut être validée (statut actuel : {feuille.statut}).",
        )
    feuille.statut = "validee"
    feuille.valide_par_id = current_user.id
    feuille.valide_le = _dt.now()
    feuille.motif_rejet = None

    from app.services.audit import log_action
    log_action(db, current_user, "feuille.valider", cible_type="feuille_temps",
               cible_id=feuille.id, cible_libelle=f"{mois:02d}/{annee} — employé {employe_id}")
    db.commit()

    emp = db.query(Employe).filter(Employe.id == employe_id).first()
    if emp and emp.utilisateur:
        from app.services.notifications import notifier
        notifier(db=db, utilisateur_id=emp.utilisateur.id, type="feuille_validee",
                 titre="Feuille de temps validée",
                 message=f"Votre feuille de {mois:02d}/{annee} a été validée.",
                 lien="/employe/pointage")
    return {"message": "Feuille validée", "statut": "validee"}


@router.post("/rejeter")
def rejeter_feuille(
    payload: RejetBody,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Rejette la feuille avec un motif : elle repasse en brouillon côté salarié."""
    _valider_periode(payload.annee, payload.mois)
    if not payload.motif.strip():
        raise HTTPException(status_code=400, detail="Le motif du rejet est requis")

    feuille = _feuille_ou_404(db, payload.employe_id, payload.annee, payload.mois)
    if feuille.statut != "soumise":
        raise HTTPException(status_code=400, detail="Seule une feuille soumise peut être rejetée")
    feuille.statut = "brouillon"
    feuille.motif_rejet = payload.motif.strip()

    from app.services.audit import log_action
    log_action(db, current_user, "feuille.rejeter", cible_type="feuille_temps",
               cible_id=feuille.id, cible_libelle=f"{payload.mois:02d}/{payload.annee}",
               details=payload.motif.strip()[:200])
    db.commit()

    emp = db.query(Employe).filter(Employe.id == payload.employe_id).first()
    if emp and emp.utilisateur:
        from app.services.notifications import notifier
        notifier(db=db, utilisateur_id=emp.utilisateur.id, type="feuille_rejetee",
                 titre="Feuille de temps à corriger",
                 message=f"Votre feuille de {payload.mois:02d}/{payload.annee} a été rejetée. "
                         f"Motif : {payload.motif.strip()}",
                 lien="/employe/pointage")
    return {"message": "Feuille rejetée", "statut": "brouillon"}


# ─── RH : récapitulatif + export ─────────────────────────────────────────────

def _rh_rows(db: Session, annee: int, mois: int) -> list[dict]:
    """Une ligne par salarié : compteurs du mois, en JOURS (demi-journées incluses)."""
    debut, fin, _ = _mois_bornes(annee, mois)
    weekdays = _weekdays(annee, mois)

    from app.routers.users import est_externe as _est_externe
    from app.services.feries import feries_du_mois, pays_employe

    def _externe(emp) -> bool:
        return _est_externe(emp.type_contrat)

    # Un calendrier par entité, calculé une seule fois pour tout le tableau
    feries_par_pays = {
        pays: feries_du_mois(db, annee, mois, pays) for pays in ("MA", "FR")
    }

    pts = db.query(Pointage).filter(
        Pointage.date_jour >= debut, Pointage.date_jour <= fin
    ).all()
    par_employe: dict[int, list[Pointage]] = {}
    for p in pts:
        par_employe.setdefault(p.employe_id, []).append(p)

    feuilles = {
        f.employe_id: f for f in db.query(FeuilleTemps).filter_by(annee=annee, mois=mois).all()
    }

    rows = []
    for emp in db.query(Employe).all():
        pays = pays_employe(emp)
        feries = feries_par_pays.get(pays, {})
        f_emp = feuilles.get(emp.id)
        t = _totaux(
            par_employe.get(emp.id, []), weekdays, feries,
            float(f_emp.jours_attendus) if f_emp and f_emp.jours_attendus is not None else None,
        )

        solde = db.query(SoldeEmploye).filter_by(
            employe_id=emp.id, type="conge_annuel", annee_reference=annee
        ).first()
        solde_reste = (float(solde.quota_total) - float(solde.consomme)) if solde else None

        u = emp.utilisateur
        feuille = feuilles.get(emp.id)
        notes = []
        if t["exceptionnel"]:
            # En tête : c'est l'information qui a un impact direct sur la paie
            notes.append(f"⚠ {t['exceptionnel']:g} j hors jours ouvrés (à majorer)")
        if t["maladie"]:
            notes.append(f"{t['maladie']:g} j maladie")
        if t["ferie"]:
            notes.append(f"{t['ferie']:g} j férié")
        if t["interne"]:
            notes.append(f"{t['interne']:g} j interne")
        if feuille and feuille.commentaire:
            notes.append(f"Note : {feuille.commentaire[:80]}")

        rows.append({
            "employe_id": emp.id,
            # Un externe est facturé au TJM : sa rémunération ne se lit pas
            # comme un salaire mensuel, la colonne doit dire laquelle des deux.
            "type": "Externe" if _externe(emp) else "Salarié",
            "est_externe": _externe(emp),
            "tjm": float(emp.tjm) if getattr(emp, "tjm", None) is not None else None,
            "nom": u.nom if u else "",
            "prenom": u.prenom if u else "",
            "sal_net": float(emp.salaire_base),
            "production": t["production"],
            "conges_payes": t["conge_paye"],
            "conges_sans_solde": t["conge_sans_solde"],
            "maladie": t["maladie"],
            "ferie": t["ferie"],
            "interne": t["interne"],
            "exceptionnel": t["exceptionnel"],
            "realise": t["realise"],
            "completion": t["completion"],
            # Base de comparaison : le RH doit voir sur quoi le taux est calculé
            "attendu": t["attendu"],
            "attendu_impose": t["attendu_impose"],
            "entite": pays,
            "commentaire_salarie": feuille.commentaire if feuille else None,
            "solde_conges": solde_reste,
            "statut": feuille.statut if feuille else "non_rempli",
            "motif_rejet": feuille.motif_rejet if feuille else None,
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


@router.get("/cra-projet")
def cra_projet(
    projet_id: int, annee: int, mois: int,
    inline: bool = False,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Pointage consolidé d'un projet : tous les salariés affectés, sur un mois."""
    from app.models.crm import Projet
    from app.services.cra import generer_pdf_cra_projet

    _valider_periode(annee, mois)
    projet = db.query(Projet).filter(Projet.id == projet_id).first()
    if not projet:
        raise HTTPException(status_code=404, detail="Projet introuvable")
    try:
        pdf = generer_pdf_cra_projet(db, projet, annee, mois)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    import re
    ref = re.sub(r"[^A-Za-z0-9]+", "", projet.reference or "PRJ")
    nom = f"Pointage_{ref}_{annee}_{mois:02d}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{nom}"'},
    )


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
    pays: str = "MA"


class FerieUpdate(BaseModel):
    date_jour: str | None = None
    libelle: str | None = None


class AttendusBody(BaseModel):
    employe_id: int
    annee: int
    mois: int
    jours_attendus: float | None = None   # null = retour au calcul automatique


@router.get("/feries")
def liste_feries(
    annee: int,
    pays: str = "MA",
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Fériés de l'année pour une entité. Les fériés calculables sont créés au besoin.

    France : tout est généré automatiquement, y compris les fêtes pascales.
    Maroc : les 9 fêtes nationales sont générées ; les fêtes religieuses
    (hégirien) doivent être saisies à la main, leur date variant chaque année.
    """
    from app.models.pointage import JourFerie
    from app.services.feries import FERIES_RELIGIEUX_LABELS, LIBELLES_PAYS, PAYS, assurer_feries

    _valider_periode(annee, 1)
    if pays not in PAYS:
        raise HTTPException(status_code=400, detail=f"Entité invalide. Valeurs : {list(PAYS)}")

    assurer_feries(db, annee, pays)
    lignes = db.query(JourFerie).filter(
        JourFerie.annee == annee, JourFerie.pays == pays
    ).order_by(JourFerie.date_jour).all()

    return {
        "annee": annee,
        "pays": pays,
        "pays_libelle": LIBELLES_PAYS.get(pays, pays),
        "feries": [
            {
                "id": f.id, "date": f.date_jour.isoformat(), "libelle": f.libelle,
                "fixe": f.fixe, "pays": f.pays,
            }
            for f in lignes
        ],
        # Seul le Maroc a des fêtes non calculables (calendrier hégirien)
        "suggestions_religieuses": FERIES_RELIGIEUX_LABELS if pays == "MA" else [],
    }


@router.put("/feries/{ferie_id}")
def modifier_ferie(
    ferie_id: int,
    payload: FerieUpdate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Corrige la date ou le libellé d'un férié (utile pour les fêtes religieuses,
    dont la date officielle n'est connue qu'à l'approche)."""
    from app.models.pointage import JourFerie

    f = db.query(JourFerie).filter(JourFerie.id == ferie_id).first()
    if not f:
        raise HTTPException(status_code=404, detail="Jour férié introuvable")

    if payload.date_jour:
        try:
            nouvelle = date.fromisoformat(payload.date_jour)
        except ValueError:
            raise HTTPException(status_code=400, detail="Date invalide")
        doublon = db.query(JourFerie).filter(
            JourFerie.date_jour == nouvelle, JourFerie.pays == f.pays, JourFerie.id != ferie_id
        ).first()
        if doublon:
            raise HTTPException(status_code=400, detail="Cette date est déjà fériée pour cette entité")
        f.date_jour = nouvelle
        f.annee = nouvelle.year
    if payload.libelle is not None:
        if not payload.libelle.strip():
            raise HTTPException(status_code=400, detail="Le libellé est requis")
        f.libelle = payload.libelle.strip()

    from app.services.audit import log_action
    log_action(db, current_user, "ferie.update", cible_type="jour_ferie", cible_id=f.id,
               cible_libelle=f"{f.date_jour.isoformat()} — {f.libelle} ({f.pays})")
    db.commit()
    return {"id": f.id, "date": f.date_jour.isoformat(), "libelle": f.libelle,
            "fixe": f.fixe, "pays": f.pays}


@router.post("/jours-attendus")
def definir_jours_attendus(
    payload: AttendusBody,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Impose (ou libère) le nombre de jours attendus d'un salarié pour un mois.

    Sert aux temps partiels, aux arrivées et départs en cours de mois — cas d'une
    démission — et aux fermetures d'entreprise. `null` rétablit le calcul
    automatique (jours ouvrés moins les fériés).
    """
    _valider_periode(payload.annee, payload.mois)
    emp = db.query(Employe).filter(Employe.id == payload.employe_id).first()
    if not emp:
        raise HTTPException(status_code=404, detail="Employé introuvable")
    if payload.jours_attendus is not None and not (0 <= payload.jours_attendus <= 31):
        raise HTTPException(status_code=400, detail="Valeur attendue entre 0 et 31")

    feuille = db.query(FeuilleTemps).filter_by(
        employe_id=payload.employe_id, annee=payload.annee, mois=payload.mois
    ).first()
    if not feuille:
        feuille = FeuilleTemps(
            employe_id=payload.employe_id, annee=payload.annee, mois=payload.mois
        )
        db.add(feuille)
    feuille.jours_attendus = payload.jours_attendus

    from app.services.audit import log_action
    log_action(db, current_user, "feuille.jours_attendus", cible_type="feuille_temps",
               cible_id=payload.employe_id,
               cible_libelle=f"{payload.mois:02d}/{payload.annee}",
               details=("automatique" if payload.jours_attendus is None
                        else f"{payload.jours_attendus:g} j imposés"))
    db.commit()
    return {
        "message": "Jours attendus mis à jour",
        "jours_attendus": float(feuille.jours_attendus) if feuille.jours_attendus is not None else None,
    }


@router.post("/feries", status_code=201)
def creer_ferie(
    payload: FerieCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    from app.models.pointage import JourFerie
    from app.services.feries import PAYS

    if payload.pays not in PAYS:
        raise HTTPException(status_code=400, detail=f"Entité invalide. Valeurs : {list(PAYS)}")
    try:
        jd = date.fromisoformat(payload.date_jour)
    except ValueError:
        raise HTTPException(status_code=400, detail="Date invalide")
    if not payload.libelle.strip():
        raise HTTPException(status_code=400, detail="Le libellé est requis")
    # L'unicité porte sur (date, entité) : le 1ᵉʳ mai peut exister pour les deux
    if db.query(JourFerie).filter(
        JourFerie.date_jour == jd, JourFerie.pays == payload.pays
    ).first():
        raise HTTPException(status_code=400, detail="Ce jour est déjà férié pour cette entité")

    f = JourFerie(date_jour=jd, libelle=payload.libelle.strip(), annee=jd.year,
                  pays=payload.pays, fixe=False)
    db.add(f)
    from app.services.audit import log_action
    log_action(db, current_user, "ferie.create", cible_type="jour_ferie",
               cible_libelle=f"{jd.isoformat()} — {payload.libelle.strip()} ({payload.pays})")
    db.commit()
    db.refresh(f)
    return {"id": f.id, "date": f.date_jour.isoformat(), "libelle": f.libelle,
            "fixe": f.fixe, "pays": f.pays}


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
        "Prime", "Production", "Congés Payés", "Congés Sans solde",
        # Jours travaillés hors jours ouvrés : base du calcul de majoration
        "Jours majorables", "Prime AID",
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

    from app.services.devises import formater_montant

    for i, r in enumerate(rows, 2):
        # La devise suit l'entité du salarié : une même colonne peut donc
        # contenir des dirhams et des euros, chacun explicitement libellé.
        # Externe : on exporte son TJM, suffixé « /j » pour qu'il ne se lise
        # jamais comme un salaire mensuel dans la colonne de paie.
        if r.get("est_externe"):
            sal = f"{formater_montant(r.get('tjm') or 0, r.get('entite'))} /j"
        else:
            sal = formater_montant(r["sal_net"], r.get("entite"))
        majorables = r.get("exceptionnel") or 0
        values = [
            r["type"], r["nom"], r["prenom"], sal, "",
            r["production"], r["conges_payes"], r["conges_sans_solde"],
            majorables or "", "",
            r["commentaire"], r["solde_conges"] if r["solde_conges"] is not None else "",
        ]
        for c, v in enumerate(values, 1):
            cell = ws.cell(row=i, column=c, value=v)
            cell.border = thin
            cell.alignment = Alignment(horizontal="center", vertical="center")
            # Mise en évidence : ces jours demandent un traitement en paie
            if c == 9 and majorables:
                cell.font = Font(bold=True, color="9A5F08")
                cell.fill = PatternFill(start_color="FDF4E3", end_color="FDF4E3", fill_type="solid")

    widths = [12, 16, 16, 20, 12, 12, 12, 14, 14, 10, 34, 12]
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
