"""CRA — Compte Rendu d'Activité (feuille des temps mensuelle en PDF).

Reprend la mise en page du modèle interne ARRA : grille jour par jour, totaux en
unités d'œuvre (1 u.o = 1 jour) et bloc de validations.

Rappel du modèle de données : seules les ABSENCES sont stockées ; un jour ouvré
sans entrée est un jour travaillé. La ligne « Production » est donc déduite.
"""
import calendar
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.models.employee import Employe
from app.models.pointage import FeuilleTemps, Pointage

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"

MOIS_FR = ["", "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
           "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"]

# Libellés affichés dans la grille, dans l'ordre d'apparition souhaité
LIBELLES = {
    "conge_paye": "Absence - Congé payé",
    "conge_sans_solde": "Absence - Congé sans solde",
    "maladie": "Absence - Maladie",
    "ferie": "Férié",
}
ORDRE = ["conge_paye", "conge_sans_solde", "maladie", "ferie"]


def _image_data_uri(valeur: str | None) -> str | None:
    """Signature/cachet en data URI (même mécanisme que pour les attestations)."""
    import base64
    import os

    if not valeur:
        return None
    chemin = os.path.join(".", valeur.lstrip("/"))
    if not os.path.exists(chemin):
        return None
    ext = chemin.rsplit(".", 1)[-1].lower() if "." in chemin else "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}.get(ext, "png")
    with open(chemin, "rb") as f:
        return f"data:image/{mime};base64,{base64.b64encode(f.read()).decode('ascii')}"


def construire_donnees_cra(db: Session, employe: Employe, annee: int, mois: int) -> dict:
    """Assemble toutes les valeurs nécessaires au rendu du CRA."""
    from app.models.parametrage import Parametrage
    from app.services.feries import feries_du_mois

    nb_jours = calendar.monthrange(annee, mois)[1]
    debut, fin = date(annee, mois, 1), date(annee, mois, nb_jours)

    jours = [
        {"numero": d, "weekend": date(annee, mois, d).weekday() >= 5}
        for d in range(1, nb_jours + 1)
    ]
    ouvres = {j["numero"] for j in jours if not j["weekend"]}

    # Absences saisies par l'employé
    saisies = db.query(Pointage).filter(
        Pointage.employe_id == employe.id,
        Pointage.date_jour >= debut, Pointage.date_jour <= fin,
    ).all()
    par_type: dict[str, dict[int, int]] = {}
    for p in saisies:
        if p.date_jour.day in ouvres:
            par_type.setdefault(p.type, {})[p.date_jour.day] = 1

    # Fériés officiels non saisis : ils comptent aussi comme non travaillés
    for iso in feries_du_mois(db, annee, mois):
        jour = date.fromisoformat(iso).day
        if jour in ouvres and not any(jour in v for v in par_type.values()):
            par_type.setdefault("ferie", {})[jour] = 1

    # Jours travaillés = jours ouvrés non couverts par une absence
    absents = {j for m in par_type.values() for j in m}
    production_jours = {j: 1 for j in sorted(ouvres - absents)}

    lignes = [{
        "libelle": "Production - Activité",
        "jours": production_jours,
        "total": len(production_jours),
    }]
    absences = []
    for cle in ORDRE:
        if cle in par_type:
            total = len(par_type[cle])
            lignes.append({"libelle": LIBELLES[cle], "jours": par_type[cle], "total": total})
            absences.append({"libelle": LIBELLES[cle].replace("Absence - ", ""), "total": total})

    feuille = db.query(FeuilleTemps).filter_by(
        employe_id=employe.id, annee=annee, mois=mois
    ).first()

    sig = db.query(Parametrage).filter(Parametrage.cle == "signature").first()
    cachet = db.query(Parametrage).filter(Parametrage.cle == "cachet").first()

    u = employe.utilisateur
    prenom_nom = f"{(u.prenom + ' ') if u and u.prenom else ''}{u.nom if u else ''}".strip()

    return {
        "prenom_nom": prenom_nom or "—",
        "matricule": employe.matricule,
        "poste": employe.poste,
        "departement": employe.departement,
        "annee": annee,
        "mois_libelle": MOIS_FR[mois],
        "nb_jours": nb_jours,
        "jours": jours,
        "jours_ouvres": len(ouvres),
        "lignes": lignes,
        "absences": absences,
        "production": len(production_jours),
        "total_absence": len(absents),
        "statut": feuille.statut if feuille else "brouillon",
        "date_soumission": (
            feuille.updated_at.strftime("%d/%m/%Y")
            if feuille and feuille.statut == "soumise" and feuille.updated_at else None
        ),
        "date_generation": datetime.now().strftime("%d/%m/%Y"),
        "signataire_nom": "El Mahdi HMOUCH",
        "signature_url": _image_data_uri(sig.valeur if sig else None),
        "cachet_url": _image_data_uri(cachet.valeur if cachet else None),
    }


def rendre_html_cra(db: Session, employe: Employe, annee: int, mois: int) -> str:
    donnees = construire_donnees_cra(db, employe, annee, mois)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    return env.get_template("cra.html").render(**donnees)


def generer_pdf_cra(db: Session, employe: Employe, annee: int, mois: int) -> bytes:
    """Renvoie le PDF en mémoire (aucun fichier écrit sur le disque)."""
    html = rendre_html_cra(db, employe, annee, mois)
    try:
        from weasyprint import HTML
        return HTML(string=html).write_pdf()
    except OSError as e:
        raise RuntimeError(f"WeasyPrint/GTK non configuré. Détail : {e}")
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Erreur de génération du CRA : {e}")


def nom_fichier_cra(employe: Employe, annee: int, mois: int) -> str:
    """Nommage aligné sur le modèle interne : CRA{matricule}_{annee}_{mois}_{NOM}.pdf"""
    import re

    u = employe.utilisateur
    nom = re.sub(r"[^A-Za-z0-9]+", "_", f"{u.nom}_{u.prenom or ''}" if u else "employe").strip("_")
    mat = re.sub(r"[^A-Za-z0-9]+", "", employe.matricule or "")
    return f"CRA{mat}_{annee}_{mois:02d}_{nom}.pdf"
