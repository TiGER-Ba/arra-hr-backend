"""Génération des contrats de travail et de prestation.

Le modèle appliqué découle du **type de contrat** du salarié :

    CDI          → contrat_cdi                 (salarié)
    CDD          → contrat_cdd                 (salarié)
    Freelance    → contrat_auto_entrepreneur   (personne physique, TJM)
    Prestataire  → contrat_prestation          (entre ARRA et une SOCIÉTÉ)
    Stage        → aucun modèle à ce jour

Comme les autres documents, le contenu vit dans la table `templates` et reste
modifiable depuis `/rh/parametrage` : ce module n'assemble que les données.
"""
from datetime import date

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.contrat import Contrat
from app.models.employee import Employe
from app.models.template import Template as TemplateModel

# Type de contrat → type de modèle en base
MODELES = {
    "CDI": "contrat_cdi",
    "CDD": "contrat_cdd",
    "Freelance": "contrat_auto_entrepreneur",
    "Prestataire": "contrat_prestation",
}

LIBELLES = {
    "contrat_cdi": "Contrat de travail à durée indéterminée",
    "contrat_cdd": "Contrat de travail à durée déterminée",
    "contrat_auto_entrepreneur": "Contrat de prestation — auto-entrepreneur",
    "contrat_prestation": "Contrat de prestation — société",
}

MOIS_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin",
           "juillet", "août", "septembre", "octobre", "novembre", "décembre"]


class ContratIndisponible(Exception):
    """Le contrat ne peut pas être produit — message destiné à l'utilisateur."""


def modele_pour(type_contrat: str | None) -> str | None:
    return MODELES.get((type_contrat or "").strip())


def _fr(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "……………"


def _fr_long(d: date | None) -> str:
    return f"{d.day} {MOIS_FR[d.month]} {d.year}" if d else "……………"


def _montant(valeur, devise: str) -> str:
    """« 7 500,00 MAD » — espace fine en séparateur de milliers."""
    if valeur is None:
        return "……………"
    return f"{float(valeur):,.2f}".replace(",", " ").replace(".", ",") + f" {devise}"


def attribuer_numero(db: Session, employe: Employe, utilisateur_id: int | None = None) -> Contrat:
    """Numéro officiel du contrat, attribué une seule fois.

    Compteur PARTAGÉ entre tous les types et remis à 1 chaque année. Si le
    salarié a déjà un contrat de ce type, son numéro est réutilisé : régénérer
    le PDF réimprime le même contrat, il n'en crée pas un nouveau.
    """
    existant = db.query(Contrat).filter_by(
        employe_id=employe.id, type_contrat=employe.type_contrat
    ).first()
    if existant:
        return existant

    annee = date.today().year
    # Quelques tentatives : deux générations simultanées viseraient le même
    # numéro, la contrainte d'unicité tranche et on repart du suivant.
    for _ in range(5):
        dernier = db.query(Contrat).filter_by(annee=annee).order_by(
            Contrat.sequence.desc()
        ).first()
        sequence = (dernier.sequence if dernier else 0) + 1
        contrat = Contrat(
            numero=f"{sequence:04d}-{annee}",
            annee=annee,
            sequence=sequence,
            employe_id=employe.id,
            type_contrat=employe.type_contrat,
            cree_par_id=utilisateur_id,
        )
        db.add(contrat)
        try:
            db.flush()
            return contrat
        except IntegrityError:
            db.rollback()
    raise ContratIndisponible(
        "Impossible d'attribuer un numéro de contrat — réessayez dans un instant."
    )


def _verifier_donnees(employe: Employe, type_modele: str) -> None:
    """Refuse de produire un contrat à trous, en nommant ce qui manque."""
    manquants: list[str] = []

    def exige(valeur, libelle):
        if valeur in (None, ""):
            manquants.append(libelle)

    u = employe.utilisateur
    exige(u.nom if u else None, "Nom")
    exige(employe.poste, "Poste")
    exige(employe.date_embauche, "Date d'embauche")
    exige(employe.date_naissance, "Date de naissance")
    exige(employe.nationalite, "Nationalité")
    exige(employe.cin, "CIN")
    exige(employe.adresse, "Adresse personnelle")

    if type_modele in ("contrat_cdi", "contrat_cdd"):
        exige(employe.salaire_base, "Salaire")
    else:
        exige(employe.tjm, "TJM")

    if type_modele == "contrat_prestation":
        # Seule la raison sociale est saisie : elle identifie le cocontractant.
        # Les mentions légales (forme, capital, RC, siège, gérant) restent des
        # colonnes facultatives — le modèle ne les affiche que si elles existent,
        # plutôt que de laisser des virgules orphelines dans la clause des parties.
        exige(employe.presta_societe, "Société prestataire")

    if manquants:
        raise ContratIndisponible(
            "Fiche incomplète pour éditer ce contrat — à renseigner : "
            + ", ".join(manquants)
        )


def construire_donnees(db: Session, employe: Employe, numero: str) -> dict:
    from app.models.parametrage import Parametrage
    from app.services.devises import devise_employe, symbole
    from app.services.pdf_generator import _logo_data_uri, image_data_uri

    u = employe.utilisateur
    prenom = (u.prenom or "").strip() if u else ""
    nom = (u.nom or "").strip() if u else ""
    devise = symbole(devise_employe(employe))
    aujourdhui = date.today()

    def image(cle: str):
        row = db.query(Parametrage).filter(Parametrage.cle == cle).first()
        return image_data_uri(row.valeur if row else None)

    return {
        "numero_contrat": numero,
        "annee": aujourdhui.year,

        # Le salarié / prestataire
        "civilite": "Mme." if (employe.sexe or "").upper() == "F" else "M.",
        "prenom": prenom,
        "nom": nom,
        "nom_complet": f"{prenom} {nom}".strip(),
        "poste": employe.poste or "",
        "adresse": employe.adresse or "",
        "date_naissance": _fr(employe.date_naissance),
        "lieu_naissance": employe.lieu_naissance or "",
        "nationalite": employe.nationalite or "",
        "cin": employe.cin or "",
        "matricule": employe.matricule,

        # Conditions
        "date_effet": _fr(employe.date_embauche),
        "date_effet_longue": _fr_long(employe.date_embauche),
        "salaire": _montant(employe.salaire_base, devise),
        "tjm": _montant(employe.tjm, devise),
        "devise": devise,

        # Société portant le prestataire
        "presta_societe": employe.presta_societe or "",
        "presta_forme": employe.presta_forme or "",
        "presta_capital": employe.presta_capital or "",
        "presta_rc": employe.presta_rc or "",
        "presta_siege": employe.presta_siege or "",
        "presta_gerant": employe.presta_gerant or "",

        # Signature
        "date_signature": _fr(aujourdhui),
        "lieu_signature": "Casablanca",
        "signataire_nom": "El Mahdi HMOUCH",
        "logo_url": _logo_data_uri(),
        "signature_url": image("signature"),
        "cachet_url": image("cachet"),
    }


def generer_pdf_contrat(db: Session, employe: Employe, utilisateur_id: int | None = None) -> tuple[bytes, str]:
    """Renvoie (pdf, nom_de_fichier). Lève ContratIndisponible si c'est impossible."""
    from jinja2 import Environment
    from weasyprint import HTML

    type_modele = modele_pour(employe.type_contrat)
    if not type_modele:
        raise ContratIndisponible(
            f"Aucun modèle de contrat pour « {employe.type_contrat or 'non défini'} ». "
            "Ajoutez-en un depuis Paramétrage › Modèles de documents."
        )

    modele = db.query(TemplateModel).filter_by(type=type_modele).first()
    if not modele:
        raise ContratIndisponible(
            f"Le modèle « {LIBELLES.get(type_modele, type_modele)} » est introuvable."
        )

    _verifier_donnees(employe, type_modele)

    contrat = attribuer_numero(db, employe, utilisateur_id)
    donnees = construire_donnees(db, employe, contrat.numero)

    try:
        html = Environment().from_string(modele.contenu_html).render(**donnees)
        pdf = HTML(string=html).write_pdf()
    except Exception as e:  # noqa: BLE001
        raise ContratIndisponible(f"Erreur de rendu du contrat : {e}")

    u = employe.utilisateur
    nom = f"{(u.nom if u else '') or 'salarie'}".replace(" ", "_")
    fichier = f"Contrat_{employe.type_contrat}_{contrat.numero}_{nom}.pdf"
    return pdf, fichier
