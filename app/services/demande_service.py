"""Création de demandes RH — logique métier centralisée.

Historiquement portée par le chatbot, elle est désormais exposée à un endpoint
REST (formulaire employé). Le chatbot ne crée plus de demandes : il ne fait que
répondre aux questions. Ce module reste la SEULE source de vérité pour :
  - la config des types de documents (`DEMANDES_CONFIG`),
  - la validation des champs,
  - la création (auto-génération, lookup bulletin dépôt, notification RH).
"""
from datetime import date

from sqlalchemy.orm import Session

from app.models.demande import Demande
from app.models.depot_document import DepotDocument
from app.services.notifications import notifier_rh

# Types auto-générés SANS validation RH.
# ⚠️ Désactivé : TOUTES les demandes passent désormais par la validation RH
# (une demande créée reste « en_attente » jusqu'à validation manuelle). Pour
# ré-autoriser l'auto-génération d'un type, le rajouter dans cet ensemble.
AUTO_GENERATION_TYPES: set[str] = set()

DEMANDES_CONFIG = {
    "bulletin_paie": {
        "label": "Bulletin de paie",
        "champs": ["mois", "annee"],
        "questions": {"mois": "Mois", "annee": "Année"},
    },
    "attestation_travail": {"label": "Attestation de travail", "champs": [], "questions": {}},
    "attestation_salaire": {"label": "Attestation de salaire", "champs": [], "questions": {}},
    "attestation_conge": {
        "label": "Attestation de congé",
        "champs": ["date_debut", "date_fin"],
        "questions": {"date_debut": "Date de début du congé", "date_fin": "Date de fin du congé"},
    },
    "demande_conge": {
        "label": "Demande de congé",
        "champs": ["type_conge", "date_debut", "nombre_jours", "motif"],
        "questions": {
            "type_conge": "Type de congé",
            "date_debut": "Date de début",
            "nombre_jours": "Nombre de jours ouvrables",
            "motif": "Motif",
        },
    },
    "ordre_mission": {
        "label": "Ordre de mission",
        "champs": ["destination", "date_depart", "date_retour", "motif"],
        "questions": {
            "destination": "Destination",
            "date_depart": "Date de départ",
            "date_retour": "Date de retour",
            "motif": "Objet de la mission",
        },
    },
    "demande_avance_salaire": {
        "label": "Demande d'avance sur salaire",
        "champs": ["montant", "motif"],
        "questions": {"montant": "Montant (MAD)", "motif": "Motif"},
    },
    "certificat_presence": {"label": "Certificat de présence", "champs": [], "questions": {}},
    "demande_formation": {
        "label": "Demande de formation",
        "champs": ["nom_formation", "organisme", "date_debut", "date_fin"],
        "questions": {
            "nom_formation": "Nom de la formation",
            "organisme": "Organisme",
            "date_debut": "Date de début",
            "date_fin": "Date de fin",
        },
    },
    "demande_mutation": {
        "label": "Demande de mutation",
        "champs": ["poste_souhaite", "departement_cible", "motif"],
        "questions": {
            "poste_souhaite": "Poste souhaité",
            "departement_cible": "Département cible",
            "motif": "Motif",
        },
    },
}

DATE_FIELDS = {"date_debut", "date_fin", "date_depart", "date_retour"}
NUMBER_FIELDS = {"nombre_jours", "montant"}

CONGE_TYPES = [
    ("annuel", "Congé annuel"),
    ("maladie", "Congé maladie"),
    ("maternite", "Congé maternité"),
    ("sans_solde", "Congé sans solde"),
]

DATE_ISO_RE = __import__("re").compile(r"^\d{4}-\d{2}-\d{2}$")


def champs_meta(type_demande: str) -> list[dict]:
    """Décrit les champs d'un type pour un formulaire dynamique (nom, label, type, options)."""
    config = DEMANDES_CONFIG.get(type_demande, {})
    metas: list[dict] = []
    for champ in config.get("champs", []):
        label = config.get("questions", {}).get(champ, champ)
        if champ == "type_conge":
            metas.append({"name": champ, "label": label, "type": "select",
                          "options": [{"value": v, "label": lab} for v, lab in CONGE_TYPES]})
        elif champ == "mois":
            metas.append({"name": champ, "label": label, "type": "select",
                          "options": [{"value": f"{m:02d}", "label": f"{m:02d}"} for m in range(1, 13)]})
        elif champ == "annee":
            metas.append({"name": champ, "label": label, "type": "number"})
        elif champ in DATE_FIELDS:
            metas.append({"name": champ, "label": label, "type": "date"})
        elif champ in NUMBER_FIELDS:
            metas.append({"name": champ, "label": label, "type": "number"})
        elif champ == "motif":
            metas.append({"name": champ, "label": label, "type": "textarea"})
        else:
            metas.append({"name": champ, "label": label, "type": "text"})
    return metas


def _valider_donnees(type_demande: str, donnees: dict, config: dict) -> list[str]:
    """Dates ISO + nombres numériques + champs requis présents."""
    erreurs = []
    for champ in config.get("champs", []):
        val = donnees.get(champ)
        if val is None or str(val).strip() == "":
            label = config.get("questions", {}).get(champ, champ)
            erreurs.append(f"Le champ « {label} » est requis")
            continue
        if champ in DATE_FIELDS:
            if not DATE_ISO_RE.match(str(val)):
                erreurs.append(f"« {champ} » doit être une date valide")
            else:
                try:
                    date.fromisoformat(str(val))
                except ValueError:
                    erreurs.append(f"« {champ} » n'est pas une date valide")
        elif champ in NUMBER_FIELDS:
            try:
                if float(val) <= 0:
                    erreurs.append(f"« {champ} » doit être un nombre positif")
            except (ValueError, TypeError):
                erreurs.append(f"« {champ} » doit être un nombre")
    return erreurs


def _chercher_bulletin_depot(db: Session, employe_id: int, donnees: dict):
    """Cherche un bulletin de paie déjà déposé pour ce mois/année."""
    mois = donnees.get("mois")
    annee = donnees.get("annee")
    q = db.query(DepotDocument).filter(
        DepotDocument.employe_id == employe_id,
        DepotDocument.categorie == "bulletin_paie",
        DepotDocument.visible_employe == True,  # noqa: E712
    )
    if mois:
        q = q.filter(DepotDocument.mois == str(mois).zfill(2))
    if annee:
        try:
            q = q.filter(DepotDocument.annee == int(annee))
        except (ValueError, TypeError):
            pass
    return q.order_by(DepotDocument.uploaded_at.desc()).first()


def _auto_generer_document(db: Session, demande: Demande) -> dict | None:
    """Génère immédiatement le PDF (passe la demande en 'validee') sans intervention RH."""
    try:
        from app.services.pdf_generator import generate_pdf
        document = generate_pdf(db=db, demande_id=demande.id, rh_id=None)
        return {"document_id": document.id, "chemin_fichier": document.chemin_fichier}
    except Exception:
        return None


def creer_demande(db: Session, employe_id: int, type_demande: str, donnees: dict,
                  conversation_id: int | None = None) -> dict:
    """Crée une demande depuis des données déjà collectées (formulaire ou autre).

    Lève ValueError (message lisible) si le type est inconnu ou les champs invalides.
    """
    config = DEMANDES_CONFIG.get(type_demande)
    if not config:
        raise ValueError("Type de demande inconnu")
    donnees = dict(donnees or {})

    # Bulletin déjà présent dans le dépôt → pas de nouvelle demande
    if type_demande == "bulletin_paie":
        depot = _chercher_bulletin_depot(db, employe_id, donnees)
        if depot:
            return {
                "demande_created": False,
                "type": type_demande,
                "label": config["label"],
                "depot_document_id": depot.id,
                "message": "Votre bulletin de paie est déjà disponible dans votre espace documents.",
            }

    erreurs = _valider_donnees(type_demande, donnees, config)
    if erreurs:
        raise ValueError(" ; ".join(erreurs))

    # Congés / formations : calcule date_fin depuis nombre_jours si absent
    if type_demande in ("demande_conge", "demande_formation"):
        nb = donnees.get("nombre_jours")
        date_debut = donnees.get("date_debut")
        if nb and date_debut and not donnees.get("date_fin"):
            from app.services.soldes import calculer_date_fin_ouvrables
            try:
                fin = calculer_date_fin_ouvrables(date_debut, int(float(nb)))
                if fin:
                    donnees["date_fin"] = fin
            except (ValueError, TypeError):
                pass

    demande = Demande(
        employe_id=employe_id,
        conversation_id=conversation_id,
        type=type_demande,
        statut="en_attente",
        donnees_collectees=donnees,
    )
    db.add(demande)
    db.commit()
    db.refresh(demande)
    label = config["label"]

    # Auto-génération des documents simples (attestations / certificat)
    if type_demande in AUTO_GENERATION_TYPES:
        result = _auto_generer_document(db, demande)
        if result:
            return {
                "demande_created": True,
                "demande_id": demande.id,
                "type": type_demande,
                "label": label,
                "statut": "validee",
                "auto_generated": True,
                "document_id": result["document_id"],
                "message": f"Votre {label.lower()} a été générée et est disponible dans vos documents.",
            }

    # Workflow RH classique → notifier les RH
    try:
        notifier_rh(
            db=db,
            type="nouvelle_demande",
            titre="Nouvelle demande",
            message=f"Nouvelle demande de {label} (#{demande.id})",
            lien="/rh/demandes",
        )
    except Exception:
        pass

    return {
        "demande_created": True,
        "demande_id": demande.id,
        "type": type_demande,
        "label": label,
        "statut": "en_attente",
        "message": f"Votre demande de {label.lower()} a été soumise. Le service RH va la traiter.",
    }
