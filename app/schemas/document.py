from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    demande_id: int
    rh_id: int | None
    template_id: int
    type: str
    chemin_fichier: str | None
    statut: str
    generated_at: datetime
    validated_at: datetime | None
