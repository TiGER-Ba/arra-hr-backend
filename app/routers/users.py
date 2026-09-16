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
Matricule auto : ARRA-I### (interne) / ARRA-E### (freelance), compteur partagé.
"""
import os
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.departement import Departement
from app.models.employee import Employe
from app.models.referentiel import (
    CATEGORIE_NATIONALITE, NATIONALITE_DEFAUT, VALEURS_INITIALES, ValeurReferentiel,
)
from app.models.rh import RH
from app.models.user import Utilisateur
from app.services.audit import log_action
from app.services.auth import get_current_user, get_password_hash, require_rh, verify_password
from app.services.email import send_email
from app.services.soldes import initialiser_soldes_par_defaut

router = APIRouter()

VALID_ROLES = {"employe", "rh", "admin"}
VALID_STATUTS = {"actif", "inactif", "suspendu"}
# Nature de l'engagement. Elle n'est PAS stockée : elle se déduit du type de
# contrat, ce qui évite qu'une colonne « externe » et le contrat se contredisent.
CONTRATS_INTERNES = ("CDI", "CDD", "Stage")
CONTRATS_EXTERNES = ("Freelance", "Prestataire")
VALID_CONTRATS = set(CONTRATS_INTERNES) | set(CONTRATS_EXTERNES)


def est_externe(type_contrat: str | None) -> bool:
    """Un externe est facturé au TJM ; un interne est salarié."""
    return (type_contrat or "").strip().lower() in {c.lower() for c in CONTRATS_EXTERNES}


VALID_SITUATIONS = {"Célibataire", "Marié(e)", "Divorcé(e)", "Veuf(ve)"}
MIN_PASSWORD_LEN = 6
INVITE_TTL_DAYS = 7

# Une fiche salarié doit être complète : ces champs conditionnent la paie, les
# attestations et le pointage. Le CNSS est la seule exception — un freelance
# n'en a pas, et un nouvel embauché ne l'obtient qu'après immatriculation.
CHAMPS_FICHE_COMMUNS = (
    ("poste", "Poste"),
    ("departement", "Département"),
    ("date_embauche", "Date d'embauche"),
    ("type_contrat", "Type de contrat"),
    ("entite", "Entité de rattachement"),
    ("date_naissance", "Date de naissance"),
    ("sexe", "Sexe"),
    ("nationalite", "Nationalité"),
    ("cin", "CIN"),
    ("telephone", "Téléphone"),
    ("adresse", "Adresse"),
)

# Un salarié touche un salaire mensuel et relève du régime social (situation
# familiale pour les charges de famille). Un externe est facturé au TJM et n'en
# relève pas : lui réclamer ces informations n'aurait aucun sens.
CHAMPS_FICHE_INTERNE = CHAMPS_FICHE_COMMUNS + (
    ("salaire_base", "Salaire"),
    ("situation_familiale", "Situation familiale"),
)
CHAMPS_FICHE_EXTERNE = CHAMPS_FICHE_COMMUNS + (
    ("tjm", "TJM"),
)


def champs_requis(type_contrat: str | None):
    return CHAMPS_FICHE_EXTERNE if est_externe(type_contrat) else CHAMPS_FICHE_INTERNE

# Facultatifs, et assumés comme tels :
#   - numero_retraite : tous les salariés ne sont pas affiliés (CIMR au Maroc) ;
#   - date_premiere_experience : carrière antérieure à ARRA, parfois inconnue.


def _valider_fiche(payload, requis=None) -> None:
    """Refuse une fiche incomplète, en nommant les champs manquants.

    Les champs exigés dépendent de la nature : salaire et situation familiale
    pour un salarié, TJM pour un externe.
    """
    requis = requis or champs_requis(getattr(payload, "type_contrat", None))
    manquants = [
        libelle for champ, libelle in requis
        if getattr(payload, champ, None) in (None, "")
    ]
    if manquants:
        nature = "externe" if est_externe(getattr(payload, "type_contrat", None)) else "salarié"
        raise HTTPException(
            status_code=400,
            detail=f"Fiche {nature} incomplète — champs requis : {', '.join(manquants)}",
        )


def _champs_selon_nature(payload) -> dict:
    """Valeurs monétaires et sociales cohérentes avec la nature de l'engagement.

    Un externe n'a ni salaire, ni situation familiale, ni CNSS : on force ces
    champs à vide plutôt que d'enregistrer ce qu'un client aurait pu envoyer,
    sinon la masse salariale et les documents reprendraient des valeurs fausses.
    """
    externe = est_externe(getattr(payload, "type_contrat", None))
    if externe:
        tjm = payload.tjm
        if tjm is None or float(tjm) <= 0:
            raise HTTPException(status_code=400, detail="Le TJM est requis pour un externe")
        return {
            "salaire_base": 0,      # colonne NOT NULL : 0 = « pas de salaire »
            "tjm": float(tjm),
            "situation_familiale": None,
            "nombre_enfants": None,
            "cnss": None,
        }

    if payload.salaire_base is None or float(payload.salaire_base) <= 0:
        raise HTTPException(status_code=400, detail="Le salaire est requis pour un salarié")
    return {
        "salaire_base": float(payload.salaire_base),
        "tjm": None,
        "situation_familiale": payload.situation_familiale,
        "nombre_enfants": _resoudre_enfants(payload.situation_familiale, payload.nombre_enfants),
        "cnss": payload.cnss,
    }


# Seule situation qui ouvre la saisie du nombre d'enfants (cf. formulaire).
SITUATION_AVEC_ENFANTS = "Marié(e)"
MAX_ENFANTS = 20


def _resoudre_enfants(situation: str | None, nombre) -> int | None:
    """Nombre d'enfants cohérent avec la situation familiale.

    Marié(e) → valeur exigée (0 accepté, et distinct de « non renseigné »).
    Toute autre situation → NULL : le champ n'est pas affiché, il ne doit donc
    pas conserver en base une valeur devenue invisible, qui ressortirait ensuite
    dans les documents générés.
    """
    if situation != SITUATION_AVEC_ENFANTS:
        return None
    if nombre in (None, ""):
        raise HTTPException(
            status_code=400,
            detail="Nombre d'enfants requis pour un salarié marié",
        )
    try:
        valeur = int(nombre)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Nombre d'enfants invalide")
    if not (0 <= valeur <= MAX_ENFANTS):
        raise HTTPException(
            status_code=400,
            detail=f"Nombre d'enfants attendu entre 0 et {MAX_ENFANTS}",
        )
    return valeur


VALID_SEXES = {"M", "F"}

# Bornes de vraisemblance : elles n'expriment aucune politique RH, elles
# attrapent les fautes de frappe (année 2026 au lieu de 1996, par exemple).
AGE_MIN = 15          # âge légal de travail au Maroc
AGE_MAX = 100


def _valider_dates(date_naissance, date_premiere_experience, date_embauche) -> None:
    """Refuse les dates impossibles, sans imposer de règle de gestion."""
    aujourdhui = date.today()

    if date_naissance:
        age = (aujourdhui - date_naissance).days / 365.25
        if age < AGE_MIN:
            raise HTTPException(
                status_code=400,
                detail=f"Date de naissance : le salarié doit avoir au moins {AGE_MIN} ans",
            )
        if age > AGE_MAX:
            raise HTTPException(status_code=400, detail="Date de naissance invalide")

    if date_premiere_experience:
        if date_premiere_experience > aujourdhui:
            raise HTTPException(
                status_code=400,
                detail="La date de première expérience ne peut pas être dans le futur",
            )
        # Une carrière ne commence pas avant l'âge légal de travail
        if date_naissance:
            annees = (date_premiere_experience - date_naissance).days / 365.25
            if annees < AGE_MIN:
                raise HTTPException(
                    status_code=400,
                    detail="La date de première expérience précède l'âge de travail du salarié",
                )
        # Elle peut en revanche être postérieure à l'embauche pour un débutant
        # recruté avant son premier poste : on ne la compare pas à date_embauche.
    _ = date_embauche


def _valider_enum(valeur: str | None, autorisees: set[str], libelle: str) -> str | None:
    if valeur in (None, ""):
        return None
    if valeur not in autorisees:
        raise HTTPException(
            status_code=400,
            detail=f"{libelle} invalide. Valeurs : {', '.join(sorted(autorisees))}",
        )
    return valeur


def _frontend_base() -> str:
    """Origine du frontend pour construire les liens d'invitation (réutilise ALLOWED_ORIGINS)."""
    return os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")[0].strip().rstrip("/")


def _generer_invitation(user: Utilisateur) -> str:
    """(Ré)génère un jeton d'invitation à usage unique et renvoie l'URL complète."""
    token = secrets.token_urlsafe(32)
    user.invite_token = token
    user.invite_token_expire = datetime.now(timezone.utc) + timedelta(days=INVITE_TTL_DAYS)
    return f"{_frontend_base()}/definir-mot-de-passe?token={token}"


def _envoyer_invitation_email(db: Session, user: Utilisateur, invite_url: str) -> bool:
    prenom = (user.prenom + " ") if user.prenom else ""
    subject = "Votre accès à la plateforme RH — ARRA Engineering"
    text = (
        f"Bonjour {prenom}{user.nom},\n\n"
        f"Un compte vient d'être créé pour vous sur la plateforme RH d'ARRA Engineering.\n"
        f"Cliquez sur le lien ci-dessous pour définir votre mot de passe :\n\n{invite_url}\n\n"
        f"Ce lien expire dans {INVITE_TTL_DAYS} jours.\n"
    )
    html = (
        f"<p>Bonjour {prenom}{user.nom},</p>"
        f"<p>Un compte vient d'être créé pour vous sur la plateforme RH d'ARRA Engineering.</p>"
        f"<p><a href=\"{invite_url}\" style=\"display:inline-block;background:#6b21a8;color:#fff;"
        f"padding:10px 18px;border-radius:8px;text-decoration:none\">Définir mon mot de passe</a></p>"
        f"<p style=\"color:#888;font-size:12px\">Ou copiez ce lien : {invite_url}<br>"
        f"Ce lien expire dans {INVITE_TTL_DAYS} jours.</p>"
    )
    return send_email(db, user.email, subject, html, text)


# ─── Schemas ────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    nom: str
    prenom: Optional[str] = None
    email: Optional[EmailStr] = None  # ARRA, auto-généré si absent
    email_personnel: Optional[EmailStr] = None  # adresse perso (Gmail…)
    mot_de_passe: Optional[str] = None  # requis SAUF si envoyer_invitation=True
    envoyer_invitation: Optional[bool] = False
    role: str  # employe | rh | admin
    est_salarie: Optional[bool] = False          # rh/admin : créer aussi une fiche salarié
    service: Optional[str] = None                # RH
    # Entité employeur : « MA » (ARRA Maroc) ou « FR » (ARRA France).
    # Détermine le calendrier de jours fériés appliqué au pointage.
    entite: Optional[str] = "MA"
    matricule: Optional[str] = None              # employé (auto si absent)
    poste: Optional[str] = None
    departement: Optional[str] = None
    salaire_base: Optional[float] = None
    tjm: Optional[float] = None
    date_embauche: Optional[date] = None
    type_contrat: Optional[str] = "CDI"
    situation_familiale: Optional[str] = None
    nombre_enfants: Optional[int] = None
    date_naissance: Optional[date] = None
    sexe: Optional[str] = None                   # M | F
    nationalite: Optional[str] = None
    date_premiere_experience: Optional[date] = None
    lieu_naissance: Optional[str] = None
    presta_societe: Optional[str] = None
    presta_forme: Optional[str] = None
    presta_capital: Optional[str] = None
    presta_rc: Optional[str] = None
    presta_siege: Optional[str] = None
    presta_gerant: Optional[str] = None
    numero_retraite: Optional[str] = None        # facultatif
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


class UserUpdate(BaseModel):
    nom: Optional[str] = None
    prenom: Optional[str] = None
    email: Optional[EmailStr] = None
    email_personnel: Optional[EmailStr] = None
    is_active: Optional[bool] = None
    service: Optional[str] = None                 # RH
    poste: Optional[str] = None                   # employé
    statut: Optional[str] = None                  # employé : actif | inactif | suspendu
    entite: Optional[str] = None                  # MA | FR
    departement: Optional[str] = None
    salaire_base: Optional[float] = None
    tjm: Optional[float] = None
    date_embauche: Optional[date] = None
    type_contrat: Optional[str] = None
    situation_familiale: Optional[str] = None
    nombre_enfants: Optional[int] = None
    date_naissance: Optional[date] = None
    sexe: Optional[str] = None
    nationalite: Optional[str] = None
    date_premiere_experience: Optional[date] = None
    lieu_naissance: Optional[str] = None
    presta_societe: Optional[str] = None
    presta_forme: Optional[str] = None
    presta_capital: Optional[str] = None
    presta_rc: Optional[str] = None
    presta_siege: Optional[str] = None
    presta_gerant: Optional[str] = None
    numero_retraite: Optional[str] = None
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


class PasswordReset(BaseModel):
    mot_de_passe: str


class FicheSalarieCreate(BaseModel):
    """Rattache une fiche salarié à un compte rh/admin existant."""
    poste: str
    departement: str
    salaire_base: Optional[float] = None
    tjm: Optional[float] = None
    date_embauche: date
    matricule: Optional[str] = None
    type_contrat: Optional[str] = "CDI"
    entite: Optional[str] = "MA"                  # MA | FR — calendrier des fériés
    situation_familiale: Optional[str] = None
    nombre_enfants: Optional[int] = None
    date_naissance: Optional[date] = None
    sexe: Optional[str] = None
    nationalite: Optional[str] = None
    date_premiere_experience: Optional[date] = None
    lieu_naissance: Optional[str] = None
    presta_societe: Optional[str] = None
    presta_forme: Optional[str] = None
    presta_capital: Optional[str] = None
    presta_rc: Optional[str] = None
    presta_siege: Optional[str] = None
    presta_gerant: Optional[str] = None
    numero_retraite: Optional[str] = None
    cin: Optional[str] = None
    cnss: Optional[str] = None
    adresse: Optional[str] = None
    telephone: Optional[str] = None


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


# Matricules : ARRA-I### pour les internes (CDI, CDD, Stage),
# ARRA-E### pour les externes (Freelance).
MOTIF_MATRICULE = re.compile(r"^ARRA-[IE]0*(\d+)$", re.IGNORECASE)
MOTIF_MATRICULE_LEGACY = re.compile(r"^EMP0*(\d+)$", re.IGNORECASE)


def lettre_matricule(type_contrat: str | None) -> str:
    """« E » pour un externe (freelance, prestataire), « I » pour un salarié."""
    return "E" if est_externe(type_contrat) else "I"


def generate_matricule(db: Session, type_contrat: str | None = None) -> str:
    """Matricule suivant : ARRA-{I|E}### .

    Le compteur est **partagé** entre internes et externes : la lettre dit la
    nature du contrat, le numéro dit l'ordre d'arrivée. La suite peut donc
    donner E001, I002, I003, E004. Une fois attribué, le matricule ne bouge
    plus — un freelance embauché en CDI garde son ARRA-E00X.
    """
    rows = db.query(Employe.matricule).all()
    max_n = 0
    for (m,) in rows:
        valeur = (m or "").strip()
        match = MOTIF_MATRICULE.match(valeur) or MOTIF_MATRICULE_LEGACY.match(valeur)
        if match:
            max_n = max(max_n, int(match.group(1)))
    return f"ARRA-{lettre_matricule(type_contrat)}{max_n + 1:03d}"


def assurer_departement(db: Session, nom: str | None) -> None:
    """Ajoute le département au référentiel s'il n'y figure pas encore.

    La liste se remplit donc d'elle-même au fil des embauches : un département
    saisi une fois est proposé à tous les suivants.
    """
    valeur = (nom or "").strip()
    if not valeur:
        return
    existe = db.query(Departement).filter(func.lower(Departement.nom) == valeur.lower()).first()
    if not existe:
        db.add(Departement(nom=valeur))


def assurer_valeur(db: Session, categorie: str, valeur: str | None) -> None:
    """Ajoute la valeur au référentiel si elle n'y figure pas (comparaison insensible à la casse)."""
    v = (valeur or "").strip()
    if not v:
        return
    existe = db.query(ValeurReferentiel).filter(
        ValeurReferentiel.categorie == categorie,
        func.lower(ValeurReferentiel.valeur) == v.lower(),
    ).first()
    if not existe:
        db.add(ValeurReferentiel(categorie=categorie, valeur=v))


def _valeurs_referentiel(db: Session, categorie: str) -> list[str]:
    """Valeurs d'une catégorie, en semant la liste initiale au premier appel."""
    lignes = db.query(ValeurReferentiel).filter(ValeurReferentiel.categorie == categorie).all()
    if not lignes:
        for v in VALEURS_INITIALES.get(categorie, ()):
            db.add(ValeurReferentiel(categorie=categorie, valeur=v))
        db.commit()
        lignes = db.query(ValeurReferentiel).filter(ValeurReferentiel.categorie == categorie).all()
    return sorted({l.valeur for l in lignes}, key=str.casefold)


def _user_to_dict(u: Utilisateur) -> dict:
    d = {
        "id": u.id,
        "nom": u.nom,
        "prenom": u.prenom,
        "email": u.email,
        "email_personnel": getattr(u, "email_personnel", None),
        "role": u.role,
        "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }
    # Fiche salarié : présente pour tout compte qui en a une (employé, ou rh/admin salarié)
    if u.employe:
        e = u.employe
        d.update({
            "employe_id": e.id, "matricule": e.matricule, "poste": e.poste,
            "departement": e.departement, "salaire_base": float(e.salaire_base),
            "tjm": float(e.tjm) if getattr(e, "tjm", None) is not None else None,
            "est_externe": est_externe(e.type_contrat),
            "date_embauche": e.date_embauche.isoformat() if e.date_embauche else None,
            "statut": e.statut, "type_contrat": e.type_contrat,
            "entite": getattr(e, "entite", None) or "MA",
            "situation_familiale": getattr(e, "situation_familiale", None),
            "nombre_enfants": getattr(e, "nombre_enfants", None),
            "date_naissance": e.date_naissance.isoformat() if getattr(e, "date_naissance", None) else None,
            "lieu_naissance": getattr(e, "lieu_naissance", None),
            "presta_societe": getattr(e, "presta_societe", None),
            "presta_forme": getattr(e, "presta_forme", None),
            "presta_capital": getattr(e, "presta_capital", None),
            "presta_rc": getattr(e, "presta_rc", None),
            "presta_siege": getattr(e, "presta_siege", None),
            "presta_gerant": getattr(e, "presta_gerant", None),
            "sexe": getattr(e, "sexe", None),
            "nationalite": getattr(e, "nationalite", None),
            "date_premiere_experience": (
                e.date_premiere_experience.isoformat()
                if getattr(e, "date_premiere_experience", None) else None
            ),
            "numero_retraite": getattr(e, "numero_retraite", None),
            "cin": e.cin, "cnss": e.cnss, "adresse": e.adresse, "telephone": e.telephone,
        })
    d["est_salarie"] = u.employe is not None
    if u.role == "rh" and u.rh:
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


# ─── Référentiel des départements ───────────────────────────────────────────
# ⚠️ Déclaré AVANT les routes « /{user_id} » : sinon FastAPI tenterait de lire
# « departements » comme un identifiant et renverrait une erreur de validation.

class DepartementCreate(BaseModel):
    nom: str


@router.get("/departements")
def liste_departements(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Départements proposés à la saisie, référentiel et fiches existantes réunis."""
    noms = {d.nom for d in db.query(Departement).all()}
    # Les fiches antérieures au référentiel ne doivent pas disparaître de la liste
    noms.update(
        d for (d,) in db.query(Employe.departement).distinct().all() if (d or "").strip()
    )
    return sorted(noms, key=str.casefold)


@router.post("/departements", status_code=status.HTTP_201_CREATED)
def creer_departement(
    payload: DepartementCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    nom = (payload.nom or "").strip()
    if not nom:
        raise HTTPException(status_code=400, detail="Le nom du département est requis")
    if len(nom) > 100:
        raise HTTPException(status_code=400, detail="Nom trop long (100 caractères maximum)")
    if db.query(Departement).filter(func.lower(Departement.nom) == nom.lower()).first():
        raise HTTPException(status_code=400, detail="Ce département existe déjà")

    db.add(Departement(nom=nom))
    log_action(db, current_user, "departement.create", cible_type="departement", cible_libelle=nom)
    db.commit()
    return {"nom": nom}


@router.get("/nationalites")
def liste_nationalites(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Nationalités proposées, référentiel et fiches existantes réunis."""
    noms = set(_valeurs_referentiel(db, CATEGORIE_NATIONALITE))
    # Une fiche peut porter une valeur retirée du référentiel : elle reste listée
    noms.update(
        n for (n,) in db.query(Employe.nationalite).distinct().all() if (n or "").strip()
    )
    return sorted(noms, key=str.casefold)


@router.post("/nationalites", status_code=status.HTTP_201_CREATED)
def creer_nationalite(
    payload: DepartementCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    nom = (payload.nom or "").strip()
    if not nom:
        raise HTTPException(status_code=400, detail="La nationalité est requise")
    if len(nom) > 60:
        raise HTTPException(status_code=400, detail="Nom trop long (60 caractères maximum)")
    deja = db.query(ValeurReferentiel).filter(
        ValeurReferentiel.categorie == CATEGORIE_NATIONALITE,
        func.lower(ValeurReferentiel.valeur) == nom.lower(),
    ).first()
    if deja:
        raise HTTPException(status_code=400, detail="Cette nationalité existe déjà")

    db.add(ValeurReferentiel(categorie=CATEGORIE_NATIONALITE, valeur=nom))
    log_action(db, current_user, "nationalite.create",
               cible_type="referentiel", cible_libelle=nom)
    db.commit()
    return {"nom": nom}


@router.post("", status_code=status.HTTP_201_CREATED)
def creer_utilisateur(
    payload: UserCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(status_code=400, detail=f"Rôle invalide. Valeurs : {sorted(VALID_ROLES)}")
    _assert_can_manage(current_user, payload.role)

    # Mot de passe : soit fourni maintenant, soit défini plus tard via invitation email
    inviter = bool(payload.envoyer_invitation)
    if inviter:
        raw_password = secrets.token_urlsafe(24)  # aléatoire : le compte reste inutilisable tant que non défini
    else:
        if not payload.mot_de_passe or len(payload.mot_de_passe) < MIN_PASSWORD_LEN:
            raise HTTPException(status_code=400, detail=f"Le mot de passe doit faire au moins {MIN_PASSWORD_LEN} caractères")
        raw_password = payload.mot_de_passe

    # Email : fourni (admin) ou auto-généré
    email = str(payload.email) if payload.email else generate_email(payload.prenom, payload.nom, db)
    if db.query(Utilisateur).filter(Utilisateur.email == email).first():
        raise HTTPException(status_code=400, detail="Cet email est déjà utilisé")

    # Fiche salarié : obligatoire pour un employé, optionnelle pour un rh/admin « salarié »
    est_salarie = bool(payload.est_salarie) and payload.role in ("rh", "admin")
    besoin_fiche = payload.role == "employe" or est_salarie
    matricule = None
    if besoin_fiche:
        _valider_fiche(payload)
        _valider_enum(payload.type_contrat, VALID_CONTRATS, "Type de contrat")
        _valider_enum(payload.situation_familiale, VALID_SITUATIONS, "Situation familiale")
        _valider_enum(payload.sexe, VALID_SEXES, "Sexe")
        _valider_dates(payload.date_naissance, payload.date_premiere_experience,
                       payload.date_embauche)
        # La lettre du matricule découle du contrat et n'en changera plus ensuite
        matricule = (payload.matricule or "").strip() or generate_matricule(db, payload.type_contrat)
        if db.query(Employe).filter(Employe.matricule == matricule).first():
            raise HTTPException(status_code=400, detail="Ce matricule est déjà utilisé")

    user = Utilisateur(
        nom=payload.nom,
        prenom=payload.prenom,
        email=email,
        email_personnel=str(payload.email_personnel) if payload.email_personnel else None,
        mot_de_passe=get_password_hash(raw_password),
        role=payload.role,
    )
    db.add(user)
    db.flush()

    if besoin_fiche:
        entite = (payload.entite or "MA").upper()
        if entite not in ("MA", "FR"):
            raise HTTPException(status_code=400, detail="Entité invalide : « MA » ou « FR »")
        emp = Employe(
            utilisateur_id=user.id,
            matricule=matricule,
            poste=payload.poste,
            departement=payload.departement,
            date_embauche=payload.date_embauche,
            type_contrat=payload.type_contrat or "CDI",
            entite=entite,
            # salaire/TJM, situation familiale, enfants et CNSS : selon la nature
            **_champs_selon_nature(payload),
            date_naissance=payload.date_naissance,
            lieu_naissance=payload.lieu_naissance,
            presta_societe=payload.presta_societe,
            presta_forme=payload.presta_forme,
            presta_capital=payload.presta_capital,
            presta_rc=payload.presta_rc,
            presta_siege=payload.presta_siege,
            presta_gerant=payload.presta_gerant,
            sexe=payload.sexe,
            nationalite=payload.nationalite or NATIONALITE_DEFAUT,
            date_premiere_experience=payload.date_premiere_experience,
            numero_retraite=(payload.numero_retraite or "").strip() or None,
            cin=payload.cin, adresse=payload.adresse, telephone=payload.telephone,
        )
        db.add(emp)
        db.flush()
        initialiser_soldes_par_defaut(db, emp.id)
        assurer_departement(db, payload.departement)
        assurer_valeur(db, CATEGORIE_NATIONALITE, emp.nationalite)
    if payload.role == "rh":
        db.add(RH(utilisateur_id=user.id, service=payload.service or "Ressources Humaines"))

    # Invitation : jeton + email (fallback lien copiable si SMTP non configuré)
    invite_url = None
    email_sent = False
    if inviter:
        invite_url = _generer_invitation(user)
        email_sent = _envoyer_invitation_email(db, user, invite_url)

    log_action(
        db, current_user, "user.create",
        cible_type="utilisateur", cible_id=user.id,
        cible_libelle=f"{user.email} ({user.role})",
        details="créé avec invitation" if inviter else "créé avec mot de passe",
    )

    db.commit()
    db.refresh(user)
    result = _user_to_dict(user)
    if inviter:
        result["invite_url"] = invite_url
        result["email_sent"] = email_sent
    return result


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
    if payload.email_personnel is not None:
        user.email_personnel = str(payload.email_personnel) or None
    if payload.is_active is not None and payload.is_active != user.is_active:
        _guard_deactivation(user, payload.is_active, current_user, db)
        user.is_active = payload.is_active
    if payload.service is not None and user.role == "rh" and user.rh:
        user.rh.service = payload.service
    if user.employe:
        e = user.employe
        if payload.statut is not None:
            if payload.statut not in VALID_STATUTS:
                raise HTTPException(status_code=400, detail=f"Statut invalide. Valeurs : {sorted(VALID_STATUTS)}")
            e.statut = payload.statut
        if payload.entite is not None:
            entite = payload.entite.upper()
            if entite not in ("MA", "FR"):
                raise HTTPException(status_code=400, detail="Entité invalide : « MA » ou « FR »")
            e.entite = entite
        _valider_enum(payload.type_contrat, VALID_CONTRATS, "Type de contrat")
        _valider_enum(payload.situation_familiale, VALID_SITUATIONS, "Situation familiale")
        _valider_enum(payload.sexe, VALID_SEXES, "Sexe")
        # Contrôle sur les valeurs RÉSULTANTES : modifier la seule date de
        # naissance doit rester cohérent avec la première expérience déjà en base.
        _valider_dates(
            payload.date_naissance if payload.date_naissance is not None else e.date_naissance,
            (payload.date_premiere_experience if payload.date_premiere_experience is not None
             else e.date_premiere_experience),
            e.date_embauche,
        )
        # ⚠️ Le matricule n'est PAS recalculé : changer de contrat ne change pas
        # d'identité. Un freelance passé en CDI garde son ARRA-E00X, sinon tous
        # les documents déjà émis à son nom cesseraient de correspondre.
        for attr in ("poste", "departement", "salaire_base", "tjm", "date_embauche",
                     "type_contrat", "situation_familiale",
                     "date_naissance", "lieu_naissance", "sexe", "nationalite",
                     "presta_societe", "presta_forme", "presta_capital",
                     "presta_rc", "presta_siege", "presta_gerant",
                     "date_premiere_experience", "numero_retraite",
                     "cin", "cnss", "adresse", "telephone"):
            val = getattr(payload, attr)
            if val is not None:
                setattr(e, attr, val)

        # Cohérence avec la nature RÉSULTANTE. Passer un salarié en freelance
        # doit vider salaire, situation familiale, enfants et CNSS — et
        # réciproquement — sinon la fiche garderait des valeurs contradictoires
        # que la masse salariale et les documents reprendraient.
        if est_externe(e.type_contrat):
            if e.tjm is None or float(e.tjm) <= 0:
                raise HTTPException(status_code=400, detail="Le TJM est requis pour un externe")
            e.salaire_base = 0
            e.situation_familiale = None
            e.nombre_enfants = None
            e.cnss = None
        else:
            e.tjm = None
            if e.salaire_base is None or float(e.salaire_base) <= 0:
                raise HTTPException(status_code=400, detail="Le salaire est requis pour un salarié")
            # Le nombre d'enfants suit la situation *résultante*, pas celle du
            # payload : sans cela, un salarié marié qui passe célibataire
            # garderait ses enfants alors que le champ disparaît du formulaire.
            if payload.situation_familiale is not None or payload.nombre_enfants is not None:
                e.nombre_enfants = _resoudre_enfants(
                    e.situation_familiale,
                    payload.nombre_enfants if payload.nombre_enfants is not None else e.nombre_enfants,
                )

        assurer_departement(db, payload.departement)
        assurer_valeur(db, CATEGORIE_NATIONALITE, payload.nationalite)

    log_action(
        db, current_user, "user.update",
        cible_type="utilisateur", cible_id=user.id,
        cible_libelle=f"{user.email} ({user.role})",
    )

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
    log_action(
        db, current_user, "user.toggle_active",
        cible_type="utilisateur", cible_id=user.id,
        cible_libelle=f"{user.email} ({user.role})",
        details="activé" if nouveau else "désactivé",
    )
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
    log_action(
        db, current_user, "user.reset_password",
        cible_type="utilisateur", cible_id=user.id,
        cible_libelle=f"{user.email} ({user.role})",
    )
    db.commit()
    return {"message": "Mot de passe réinitialisé"}


@router.post("/{user_id}/employe")
def ajouter_fiche_salarie(
    user_id: int,
    payload: FicheSalarieCreate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Rattache une fiche salarié (congés, attestations, bulletins) à un compte rh/admin.

    Réservé de fait à l'admin (un RH ne gère que les comptes employés).
    """
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    _assert_can_manage(current_user, user.role)
    if user.employe:
        raise HTTPException(status_code=400, detail="Ce compte a déjà une fiche salarié")

    _valider_fiche(payload)
    _valider_enum(payload.type_contrat, VALID_CONTRATS, "Type de contrat")
    _valider_enum(payload.situation_familiale, VALID_SITUATIONS, "Situation familiale")
    _valider_enum(payload.sexe, VALID_SEXES, "Sexe")
    _valider_dates(payload.date_naissance, payload.date_premiere_experience,
                   payload.date_embauche)

    matricule = (payload.matricule or "").strip() or generate_matricule(db, payload.type_contrat)
    if db.query(Employe).filter(Employe.matricule == matricule).first():
        raise HTTPException(status_code=400, detail="Ce matricule est déjà utilisé")

    entite = (payload.entite or "MA").upper()
    if entite not in ("MA", "FR"):
        raise HTTPException(status_code=400, detail="Entité invalide (MA ou FR)")

    emp = Employe(
        utilisateur_id=user.id,
        matricule=matricule,
        poste=payload.poste,
        departement=payload.departement,
        date_embauche=payload.date_embauche,
        type_contrat=payload.type_contrat or "CDI",
        entite=entite,
        # salaire/TJM, situation familiale, enfants et CNSS : selon la nature
        **_champs_selon_nature(payload),
        date_naissance=payload.date_naissance,
        lieu_naissance=payload.lieu_naissance,
        presta_societe=payload.presta_societe,
        presta_forme=payload.presta_forme,
        presta_capital=payload.presta_capital,
        presta_rc=payload.presta_rc,
        presta_siege=payload.presta_siege,
        presta_gerant=payload.presta_gerant,
        sexe=payload.sexe,
        nationalite=payload.nationalite or NATIONALITE_DEFAUT,
        date_premiere_experience=payload.date_premiere_experience,
        numero_retraite=(payload.numero_retraite or "").strip() or None,
        cin=payload.cin, adresse=payload.adresse, telephone=payload.telephone,
    )
    db.add(emp)
    db.flush()
    initialiser_soldes_par_defaut(db, emp.id)
    assurer_departement(db, payload.departement)
    assurer_valeur(db, CATEGORIE_NATIONALITE, emp.nationalite)
    log_action(
        db, current_user, "user.add_employe",
        cible_type="utilisateur", cible_id=user.id,
        cible_libelle=f"{user.email} ({user.role})",
        details=f"fiche salarié {matricule}",
    )
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


@router.post("/{user_id}/invite")
def inviter_utilisateur(
    user_id: int,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """(Re)génère un lien d'invitation et l'envoie par email si le SMTP est configuré.

    Renvoie toujours `invite_url` pour que le RH/admin puisse copier le lien si
    l'email n'a pas pu être envoyé (SMTP non configuré).
    """
    user = db.query(Utilisateur).filter(Utilisateur.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Utilisateur introuvable")
    _assert_can_manage(current_user, user.role)

    invite_url = _generer_invitation(user)
    email_sent = _envoyer_invitation_email(db, user, invite_url)
    log_action(
        db, current_user, "user.invite",
        cible_type="utilisateur", cible_id=user.id,
        cible_libelle=f"{user.email} ({user.role})",
        details="email envoyé" if email_sent else "lien généré (email non envoyé)",
    )
    db.commit()
    return {"invite_url": invite_url, "email_sent": email_sent}


def _purger_donnees_employe(db: Session, emp: Employe) -> None:
    """Supprime toutes les données rattachées à une fiche salarié avant de la
    supprimer : demandes (+ documents générés), conversations (+ messages),
    soldes (+ mouvements), dépôt documentaire. Les FK sans ON DELETE (demandes,
    documents) exigent une purge explicite."""
    from app.models.conversation import Conversation
    from app.models.demande import Demande
    from app.models.depot_document import DepotDocument
    from app.models.document import Document as DocModel
    from app.models.message import Message
    from app.models.pointage import FeuilleTemps, Pointage
    from app.models.solde import MouvementSolde, SoldeEmploye

    emp_id = emp.id
    db.query(Pointage).filter(Pointage.employe_id == emp_id).delete(synchronize_session=False)
    db.query(FeuilleTemps).filter(FeuilleTemps.employe_id == emp_id).delete(synchronize_session=False)
    demande_ids = [r[0] for r in db.query(Demande.id).filter(Demande.employe_id == emp_id).all()]
    if demande_ids:
        db.query(DocModel).filter(DocModel.demande_id.in_(demande_ids)).delete(synchronize_session=False)
        db.query(Demande).filter(Demande.id.in_(demande_ids)).delete(synchronize_session=False)
    conv_ids = [r[0] for r in db.query(Conversation.id).filter(Conversation.employe_id == emp_id).all()]
    if conv_ids:
        db.query(Message).filter(Message.conversation_id.in_(conv_ids)).delete(synchronize_session=False)
        db.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
    solde_ids = [r[0] for r in db.query(SoldeEmploye.id).filter(SoldeEmploye.employe_id == emp_id).all()]
    if solde_ids:
        db.query(MouvementSolde).filter(MouvementSolde.solde_id.in_(solde_ids)).delete(synchronize_session=False)
        db.query(SoldeEmploye).filter(SoldeEmploye.id.in_(solde_ids)).delete(synchronize_session=False)
    db.query(DepotDocument).filter(DepotDocument.employe_id == emp_id).delete(synchronize_session=False)
    db.delete(emp)


def _detacher_refs_rh(db: Session, rh: RH) -> None:
    """Détache (sans les supprimer) les documents générés par ce RH — on conserve
    le document, on retire juste l'auteur — puis supprime la fiche RH. Le dépôt et
    les mouvements pointant vers ce RH passent en NULL (ON DELETE SET NULL en base)."""
    from app.models.document import Document as DocModel
    db.query(DocModel).filter(DocModel.rh_id == rh.id).update(
        {DocModel.rh_id: None}, synchronize_session=False
    )
    db.delete(rh)


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

    libelle = f"{user.email} ({user.role})"
    cible_id = user.id
    try:
        # Un compte peut cumuler une fiche salarié ET une fiche RH (rh/admin salarié)
        if user.employe:
            _purger_donnees_employe(db, user.employe)
        if user.rh:
            _detacher_refs_rh(db, user.rh)
        db.delete(user)
        log_action(
            db, current_user, "user.delete",
            cible_type="utilisateur", cible_id=cible_id, cible_libelle=libelle,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Suppression impossible (dépendances liées). Désactivez plutôt le compte.",
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
    from app.services.security import read_upload_limited

    data = read_upload_limited(fichier, settings.MAX_UPLOAD_MB, {".png", ".jpg", ".jpeg", ".webp", ".pdf"})
    try:
        from app.services.id_ocr import extract_id_fields
    except Exception:
        raise HTTPException(status_code=503, detail="Module OCR indisponible sur le serveur")
    return extract_id_fields(data, fichier.filename or "")
