from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    demande_id: int
    type: str
    statut: str
    generated_at: datetime


class DemandeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employe_id: int
    conversation_id: int | None
    type: str
    statut: str
    donnees_collectees: dict
    raison_rejet: str | None
    nom_employe: str | None = None
    matricule: str | None = None
    created_at: datetime
    updated_at: datetime
    documents: list[DocumentOut] = []

    @classmethod
    def from_orm_with_employe(cls, demande):
        data = cls.model_validate(demande)
        if demande.employe and demande.employe.utilisateur:
            data.nom_employe = demande.employe.utilisateur.nom
            data.matricule = demande.employe.matricule
        return data


class DemandeRejeter(BaseModel):
    raison: str


class DemandeListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    statut: str
    created_at: datetime
    nom_employe: str | None = None
    matricule: str | None = None
