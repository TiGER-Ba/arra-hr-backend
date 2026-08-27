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

    # Saisies du mois (production sur projet, absences, activités internes)
    saisies = db.query(Pointage).filter(
        Pointage.employe_id == employe.id,
        Pointage.date_jour >= debut, Pointage.date_jour <= fin,
    ).all()

    # Regroupement en lignes de grille, comme dans la feuille de temps
    from app.models.crm import Projet
    from app.models.pointage import LIBELLES_INTERNE

    groupes: dict[tuple, dict] = {}
    for p in saisies:
        if p.date_jour.day not in ouvres:
            continue
        categorie = p.categorie or "absence"
        cle = (categorie, p.projet_id, p.type)
        if cle not in groupes:
            if categorie == "production" and p.projet_id:
                projet = db.query(Projet).filter(Projet.id == p.projet_id).first()
                libelle = projet.libelle if projet else f"Projet #{p.projet_id}"
            elif categorie == "interne":
                libelle = f"Interne - {LIBELLES_INTERNE.get(p.type, p.type.capitalize())}"
            else:
                libelle = LIBELLES.get(p.type, f"Absence - {p.type}")
            groupes[cle] = {"libelle": libelle, "categorie": categorie, "jours": {}, "total": 0.0}
        val = float(p.valeur or 1)
        groupes[cle]["jours"][p.date_jour.day] = val
        groupes[cle]["total"] += val

    # Fériés officiels non saisis : non travaillés, sans écraser une saisie
    couverts = {j for g in groupes.values() for j in g["jours"]}
    for iso in feries_du_mois(db, annee, mois):
        jour = date.fromisoformat(iso).day
        if jour in ouvres and jour not in couverts:
            cle = ("absence", None, "ferie")
            groupes.setdefault(cle, {"libelle": LIBELLES["ferie"], "categorie": "absence",
                                     "jours": {}, "total": 0.0})
            groupes[cle]["jours"][jour] = 1.0
            groupes[cle]["total"] += 1.0

    # Jours ouvrés non couverts = production non affectée à un projet
    couverts = {j for g in groupes.values() for j in g["jours"]}
    restants = {j: 1.0 for j in sorted(ouvres - couverts)}
    if restants:
        groupes[("production", None, "normale")] = {
            "libelle": "Production - Activité", "categorie": "production",
            "jours": restants, "total": float(len(restants)),
        }

    ordre_cat = {"production": 0, "interne": 1, "absence": 2}
    lignes = sorted(groupes.values(), key=lambda g: (ordre_cat.get(g["categorie"], 9), g["libelle"]))

    absences = [
        {"libelle": g["libelle"].replace("Absence - ", ""), "total": g["total"]}
        for g in lignes if g["categorie"] == "absence"
    ]
    # Totaux en JOURS et non en nombre de lignes : une demi-journée vaut 0,5
    total_production = sum(g["total"] for g in lignes if g["categorie"] == "production")
    total_absences = sum(g["total"] for g in lignes if g["categorie"] == "absence")
    total_interne = sum(g["total"] for g in lignes if g["categorie"] == "interne")

    # Jours de repos travaillés : à faire apparaître, ils ouvrent droit à majoration
    feries_iso = set(feries_du_mois(db, annee, mois).keys())
    exceptionnels = [
        {
            "jour": p.date_jour.day,
            "date": p.date_jour.strftime("%d/%m/%Y"),
            "valeur": f"{float(p.valeur or 1):g}",
            "motif": "Jour férié" if p.date_jour.isoformat() in feries_iso else "Week-end",
            "commentaire": p.commentaire,
        }
        for p in sorted(saisies, key=lambda x: x.date_jour)
        if (p.categorie or "absence") != "absence"
        and (p.date_jour.weekday() >= 5 or p.date_jour.isoformat() in feries_iso)
    ]
    total_exceptionnel = sum(float(p.valeur or 1) for p in saisies
                             if (p.categorie or "absence") != "absence"
                             and (p.date_jour.weekday() >= 5 or p.date_jour.isoformat() in feries_iso))

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
        "production": f"{total_production:g}",
        "total_absence": f"{total_absences:g}",
        "total_interne": f"{total_interne:g}",
        "exceptionnels": exceptionnels,
        "total_exceptionnel": f"{total_exceptionnel:g}",
        "commentaire_salarie": feuille.commentaire if feuille else None,
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
