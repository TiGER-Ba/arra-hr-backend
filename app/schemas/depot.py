from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DepotDocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employe_id: int
    uploaded_by_rh_id: int | None
    categorie: str
    nom_fichier: str
    description: str | None
    mois: str | None
    annee: int | None
    visible_employe: bool
    uploaded_at: datetime


class DepotDocumentDetail(DepotDocumentOut):
    nom_employe: str | None = None
    matricule: str | None = None
    uploaded_by_nom: str | None = None
