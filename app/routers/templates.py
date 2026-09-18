"""Édition des modèles de documents depuis /rh/parametrage.

Les modèles sont du **HTML + Jinja2** : l'éditeur les expose tels quels. Un
éditeur de texte enrichi détruirait les balises `{{ }}` et `{% %}`, donc la
page présente le code, la liste des variables et un aperçu.

⚠️ Modifier un modèle met `personnalise = True` : `seed.py` cesse alors de
l'écraser au démarrage avec la version du dépôt. « Réinitialiser » rend la main
au fichier de référence.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.template import Template as TemplateModel
from app.models.user import Utilisateur
from app.services.audit import log_action
from app.services.auth import require_admin, require_rh

router = APIRouter()

DOSSIER_MODELES = Path(__file__).resolve().parent.parent / "templates"

# Variables offertes au modèle, par famille. Sert uniquement d'aide à la saisie
# dans l'éditeur — le rendu, lui, accepte tout ce que le service fournit.
VARIABLES_COMMUNES = [
    ("nom_employe", "Nom du salarié"),
    ("matricule", "Matricule"),
    ("poste", "Poste"),
    ("departement", "Département"),
    ("date_embauche", "Date d'embauche / d'intégration"),
    ("cin", "CIN"),
    ("adresse", "Adresse personnelle"),
    ("telephone", "Téléphone"),
    ("devise", "Devise (MAD ou €)"),
    ("date_generation", "Date du jour"),
    ("lieu_signature", "Lieu de signature"),
    ("signataire_nom", "Nom du signataire"),
    ("logo_url", "Logo ARRA"),
    ("signature_url", "Signature scannée"),
    ("cachet_url", "Cachet scanné"),
]

VARIABLES_CONTRAT = [
    ("numero_contrat", "Numéro du contrat (0045-2026)"),
    ("civilite", "M. ou Mme."),
    ("prenom", "Prénom"),
    ("nom", "Nom"),
    ("nom_complet", "Prénom et nom"),
    ("date_naissance", "Date de naissance"),
    ("lieu_naissance", "Lieu de naissance"),
    ("nationalite", "Nationalité"),
    ("date_effet", "Date d'effet / début de mission"),
    ("salaire", "Salaire mensuel avec devise"),
    ("tjm", "Tarif journalier avec devise"),
    ("date_signature", "Date de signature"),
]

VARIABLES_SOCIETE = [
    ("presta_societe", "Raison sociale du prestataire"),
    ("presta_forme", "Forme juridique"),
    ("presta_capital", "Capital social"),
    ("presta_rc", "Registre de commerce"),
    ("presta_siege", "Siège social"),
    ("presta_gerant", "Gérant"),
]


def _variables(type_modele: str) -> list[dict]:
    if type_modele.startswith("contrat_"):
        liste = VARIABLES_CONTRAT + VARIABLES_COMMUNES
        if type_modele == "contrat_prestation":
            liste = VARIABLES_CONTRAT + VARIABLES_SOCIETE + VARIABLES_COMMUNES
    else:
        liste = VARIABLES_COMMUNES
    return [{"nom": n, "description": d} for n, d in liste]


def _fichier_reference(type_modele: str) -> Path:
    """Chemin du modèle de référence, avec garde-fou contre la traversée."""
    chemin = (DOSSIER_MODELES / f"{type_modele}.html").resolve()
    if not str(chemin).startswith(str(DOSSIER_MODELES.resolve())):
        raise HTTPException(status_code=400, detail="Modèle invalide")
    return chemin


class ModeleUpdate(BaseModel):
    contenu_html: str


@router.get("")
def liste_modeles(
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Modèles disponibles, contrats compris — sans leur contenu.

    ⚠️ Les types qui ne se génèrent plus sont **masqués** : le bulletin de paie
    est établi par le comptable, son modèle ne produit plus rien. Le laisser
    dans la liste inviterait à passer du temps sur un document mort. La ligne en
    base est conservée — les bulletins générés avant y font référence.
    """
    from app.services.demande_service import se_genere

    lignes = [t for t in db.query(TemplateModel).order_by(TemplateModel.type).all()
              if se_genere(t.type)]
    return [
        {
            "type": t.type,
            "nom": t.nom,
            "actif": t.actif,
            "personnalise": bool(getattr(t, "personnalise", False)),
            "est_contrat": t.type.startswith("contrat_"),
            "taille": len(t.contenu_html or ""),
        }
        for t in lignes
    ]


@router.get("/{type_modele}")
def detail_modele(
    type_modele: str,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    t = db.query(TemplateModel).filter(TemplateModel.type == type_modele).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modèle introuvable")
    return {
        "type": t.type,
        "nom": t.nom,
        "contenu_html": t.contenu_html,
        "personnalise": bool(getattr(t, "personnalise", False)),
        "est_contrat": t.type.startswith("contrat_"),
        "reference_disponible": _fichier_reference(t.type).exists(),
        "variables": _variables(t.type),
    }


@router.put("/{type_modele}")
def modifier_modele(
    type_modele: str,
    payload: ModeleUpdate,
    current_user: Utilisateur = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enregistre le modèle et le protège de la resynchronisation au démarrage."""
    from jinja2 import TemplateSyntaxError

    from app.services.rendu import environnement_modele

    t = db.query(TemplateModel).filter(TemplateModel.type == type_modele).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modèle introuvable")
    if not (payload.contenu_html or "").strip():
        raise HTTPException(status_code=400, detail="Le contenu ne peut pas être vide")

    # Un modèle au Jinja invalide ferait échouer toutes les générations à venir
    try:
        environnement_modele().parse(payload.contenu_html)
    except TemplateSyntaxError as e:
        raise HTTPException(
            status_code=400,
            detail=f"Erreur de syntaxe ligne {e.lineno} : {e.message}",
        )

    t.contenu_html = payload.contenu_html
    t.personnalise = True
    log_action(db, current_user, "template.update", cible_type="template",
               cible_id=t.id, cible_libelle=t.nom)
    db.commit()
    return {"message": "Modèle enregistré", "personnalise": True}


class CorpsUpdate(BaseModel):
    corps: str


@router.get("/{type_modele}/visuel")
def modele_visuel(
    type_modele: str,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Modèle préparé pour l'édition visuelle : corps éditable, style, blocs.

    Le style et la logique Jinja ne sont **pas** envoyés comme éditables : le
    corps ne contient que la prose et des jetons opaques à la place des blocs.
    """
    from app.services.modele_visuel import decouper

    t = db.query(TemplateModel).filter(TemplateModel.type == type_modele).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modèle introuvable")

    decoupe = decouper(t.contenu_html)
    return {
        "type": t.type,
        "nom": t.nom,
        "corps": decoupe["corps"],
        "style": decoupe["style"],
        "blocs": [
            {"index": i, "etiquette": b["etiquette"]}
            for i, b in enumerate(decoupe["blocs"])
        ],
        "personnalise": bool(getattr(t, "personnalise", False)),
        "variables": _variables(t.type),
    }


@router.put("/{type_modele}/visuel")
def enregistrer_visuel(
    type_modele: str,
    payload: CorpsUpdate,
    current_user: Utilisateur = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enregistre le corps édité visuellement, en réinjectant style et blocs.

    ⚠️ Le client n'envoie que le CORPS. Le prologue, l'épilogue et la source des
    blocs sont relus du modèle enregistré : un éditeur de texte enrichi ne peut
    donc ni abîmer le CSS, ni altérer une condition Jinja.
    """
    from jinja2 import TemplateSyntaxError

    from app.services.modele_visuel import recomposer
    from app.services.rendu import environnement_modele

    t = db.query(TemplateModel).filter(TemplateModel.type == type_modele).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modèle introuvable")
    if not (payload.corps or "").strip():
        raise HTTPException(status_code=400, detail="Le document ne peut pas être vide")

    complet = recomposer(t.contenu_html, payload.corps)

    # Filet de sécurité : malgré la protection des blocs, on ne remplace jamais
    # un modèle par quelque chose que Jinja refuserait ensuite de rendre.
    try:
        environnement_modele().parse(complet)
    except TemplateSyntaxError as e:
        raise HTTPException(
            status_code=400,
            detail=f"La mise en forme a produit un modèle invalide (ligne {e.lineno} : "
                   f"{e.message}). Vos modifications n'ont pas été enregistrées.",
        )

    t.contenu_html = complet
    t.personnalise = True
    log_action(db, current_user, "template.update_visuel", cible_type="template",
               cible_id=t.id, cible_libelle=t.nom)
    db.commit()
    return {"message": "Document enregistré", "personnalise": True}


@router.post("/{type_modele}/reinitialiser")
def reinitialiser_modele(
    type_modele: str,
    current_user: Utilisateur = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Rétablit la version de référence livrée avec l'application."""
    t = db.query(TemplateModel).filter(TemplateModel.type == type_modele).first()
    if not t:
        raise HTTPException(status_code=404, detail="Modèle introuvable")

    fichier = _fichier_reference(type_modele)
    if not fichier.exists():
        raise HTTPException(
            status_code=400,
            detail="Aucune version de référence pour ce modèle — il a été créé à la main.",
        )

    t.contenu_html = fichier.read_text(encoding="utf-8")
    t.personnalise = False
    log_action(db, current_user, "template.reset", cible_type="template",
               cible_id=t.id, cible_libelle=t.nom)
    db.commit()
    return {"message": "Modèle réinitialisé", "contenu_html": t.contenu_html}


@router.post("/{type_modele}/apercu")
def apercu_modele(
    type_modele: str,
    payload: ModeleUpdate,
    current_user: Utilisateur = Depends(require_rh),
    db: Session = Depends(get_db),
):
    """Rendu du modèle avec un jeu de données d'exemple, sans rien enregistrer."""
    from app.services.rendu import rendre_modele

    exemple = {v["nom"]: f"[{v['nom']}]" for v in _variables(type_modele)}
    exemple.update({
        "nom_employe": "BENJELLOUN Karim", "nom_complet": "Karim BENJELLOUN",
        "nom": "BENJELLOUN", "prenom": "Karim", "civilite": "M.",
        "poste": "Ingénieur Logiciel", "departement": "Informatique",
        "matricule": "ARRA-I001", "cin": "BK284571",
        "adresse": "12 rue des Lilas, Casablanca", "telephone": "+212 661 23 45 67",
        "date_naissance": "15/03/1994", "lieu_naissance": "Casablanca",
        "nationalite": "Marocaine", "devise": "MAD",
        "date_embauche": "01/09/2026", "date_effet": "01/09/2026",
        "salaire": "9 500,00 MAD", "tjm": "2 500,00 MAD",
        "salaire_base": 9500.0,
        "numero_contrat": "0045-2026",
        "date_generation": "16/09/2026", "date_signature": "16/09/2026",
        "lieu_signature": "Casablanca", "signataire_nom": "El Mahdi HMOUCH",
        "presta_societe": "I-ETERIA", "presta_forme": "SARL",
        "presta_capital": "300.000 MAD", "presta_rc": "115899",
        "presta_siege": "2 Place Abou Baker Essadiq, App 10 — Rabat",
        "presta_gerant": "M. BEGDOURI ACHKARI Mohammed Amine",
        "logo_url": None, "signature_url": None, "cachet_url": None,
    })

    try:
        html = rendre_modele(payload.contenu_html, **exemple)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Erreur de rendu : {e}")
    return {"html": html}
