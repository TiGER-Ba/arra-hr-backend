import base64
import os
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy.orm import Session

from app.config import settings
from app.models.demande import Demande
from app.models.document import Document
from app.models.employee import Employe
from app.models.parametrage import Parametrage
from app.models.template import Template as TemplateModel

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
STATIC_DIR = Path(__file__).parent.parent / "static"


def _logo_data_uri() -> str | None:
    """Logo ARRA embarqué en base64 : aucun accès réseau ni chemin absolu requis
    par WeasyPrint, et le rendu est identique en aperçu HTML et en PDF."""
    chemin = STATIC_DIR / "logo_arra.png"
    if not chemin.exists():
        return None
    return "data:image/png;base64," + base64.b64encode(chemin.read_bytes()).decode("ascii")

# Fields that must stay numeric for template rendering
_NUMERIC_FIELDS = {"salaire_base"}


def _load_base_data(db: Session, demande_id: int) -> tuple[Demande, TemplateModel, dict]:
    demande = db.query(Demande).filter(Demande.id == demande_id).first()
    if not demande:
        raise ValueError(f"Demande {demande_id} introuvable")

    employe = db.query(Employe).filter(Employe.id == demande.employe_id).first()
    if not employe:
        raise ValueError(f"Employé introuvable pour la demande {demande_id}")

    utilisateur = employe.utilisateur

    template_db = (
        db.query(TemplateModel)
        .filter(TemplateModel.type == demande.type, TemplateModel.actif == True)
        .first()
    )
    if not template_db:
        raise ValueError(f"Aucun template actif pour le type : {demande.type}")

    sig_row = db.query(Parametrage).filter(Parametrage.cle == "signature").first()
    cachet_row = db.query(Parametrage).filter(Parametrage.cle == "cachet").first()

    def _to_abs(url_path: str | None) -> str | None:
        # Embarque l'image en data URI base64 : fonctionne à la fois dans
        # l'aperçu HTML (navigateur) ET dans le PDF WeasyPrint, sans dépendre
        # d'un chemin file:// (invisible côté navigateur) ni de l'URL du serveur.
        if not url_path:
            return None
        rel = url_path.lstrip("/")
        abs_path = Path(rel).resolve()
        if not abs_path.exists():
            return None
        ext = abs_path.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "svg": "svg+xml"}.get(ext, "png")
        data = base64.b64encode(abs_path.read_bytes()).decode("ascii")
        return f"data:image/{mime};base64,{data}"

    from app.services.devises import devise_employe, symbole

    entite = (getattr(employe, "entite", None) or "MA").upper()
    devise = symbole(devise_employe(employe))

    base = {
        "nom_employe": utilisateur.nom,
        "email_employe": utilisateur.email,
        "matricule": employe.matricule,
        "poste": employe.poste,
        "departement": employe.departement,
        "salaire_base": float(employe.salaire_base),
        "date_embauche": employe.date_embauche.strftime("%d/%m/%Y"),
        "statut_employe": employe.statut,
        "type_contrat": employe.type_contrat or "CDI",
        "situation_familiale": getattr(employe, "situation_familiale", None) or "—",
        # Nul si le salarié n'est pas marié : le template ne doit rien afficher
        "nombre_enfants": getattr(employe, "nombre_enfants", None),
        # Entité employeur et devise associée : un salarié ARRA France doit voir
        # des euros sur son bulletin, pas des dirhams.
        "entite": entite,
        "entite_libelle": "ARRA ENGINEERING France" if entite == "FR" else "ARRA ENGINEERING SARL (AU)",
        "devise": devise,
        "cin": employe.cin or "—",
        "cnss": employe.cnss or "—",
        "adresse": employe.adresse or "—",
        "telephone": employe.telephone or "—",
        "date_generation": datetime.now().strftime("%d/%m/%Y"),
        "lieu_signature": "Paris" if entite == "FR" else "Casablanca",
        "signataire_nom": "El Mahdi HMOUCH",
        "signataire_fonction": f"Directeur ARRA ENGINEERING {'France' if entite == 'FR' else 'Maroc'}",
        "signature_url": _to_abs(sig_row.valeur if sig_row else None),
        "cachet_url": _to_abs(cachet_row.valeur if cachet_row else None),
        # Logo ARRA en en-tête des documents générés
        "logo_url": _logo_data_uri(),
    }

    # ⚠️ Le bulletin de paie applique le barème MAROCAIN (CNSS 4,48 %, AMO
    # 2,26 %, IR). Pour un salarié ARRA France, il faudrait les cotisations
    # françaises (URSSAF, retraite complémentaire, CSG/CRDS) : changer la seule
    # devise produirait un bulletin en euros aux taux marocains, c'est-à-dire un
    # document faux. On refuse plutôt que d'en émettre un.
    if demande.type == "bulletin_paie" and entite == "FR":
        raise RuntimeError(
            "Bulletin de paie indisponible pour un salarié ARRA France : le barème "
            "de cotisations françaises n'est pas encore paramétré. Déposez le "
            "bulletin établi par votre gestionnaire de paie dans « Documents »."
        )

    # donnees_collectees overrides base — but restore numeric types
    donnees = {**base, **demande.donnees_collectees}
    for field in _NUMERIC_FIELDS:
        if field in donnees:
            try:
                donnees[field] = float(donnees[field])
            except (ValueError, TypeError):
                donnees[field] = base.get(field, 0.0)

    return demande, template_db, donnees


def render_html(db: Session, demande_id: int) -> tuple[str, dict]:
    """Render template to HTML and return (html, donnees_as_strings)."""
    demande, template_db, donnees = _load_base_data(db, demande_id)
    try:
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        jinja_template = env.from_string(template_db.contenu_html)
        html_content = jinja_template.render(**donnees)
    except Exception as e:
        raise RuntimeError(f"Erreur de rendu du template : {e}")
    # Return string-serialized donnees for frontend form
    donnees_str = {k: str(v) for k, v in donnees.items()}
    return html_content, donnees_str


def generate_pdf(db: Session, demande_id: int, rh_id: int | None = None) -> Document:
    demande, template_db, donnees = _load_base_data(db, demande_id)

    try:
        env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
        jinja_template = env.from_string(template_db.contenu_html)
        html_content = jinja_template.render(**donnees)
    except Exception as e:
        raise RuntimeError(f"Erreur de rendu du template : {e}")

    employe = db.query(Employe).filter(Employe.id == demande.employe_id).first()
    uploads_dir = settings.UPLOADS_DIR
    os.makedirs(uploads_dir, exist_ok=True)
    filename = f"{demande.type}_{employe.matricule}_{demande.id}.pdf"
    filepath = os.path.join(uploads_dir, filename)

    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(filepath)
    except OSError as e:
        raise RuntimeError(
            f"WeasyPrint/GTK non configuré. Installez GTK3 Runtime. Détail : {e}"
        )
    except Exception as e:
        raise RuntimeError(f"Erreur WeasyPrint : {e}")

    document = Document(
        demande_id=demande_id,
        rh_id=rh_id,
        template_id=template_db.id,
        type=demande.type,
        chemin_fichier=filepath,
        statut="genere",
        validated_at=datetime.now(),
    )
    db.add(document)
    demande.statut = "validee"
    db.commit()
    db.refresh(document)
    return document
