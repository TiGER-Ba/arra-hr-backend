from datetime import datetime
from pydantic import BaseModel, ConfigDict


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    utilisateur_id: int
    type: str
    titre: str
    message: str
    lien: str | None
    lu: bool
    created_at: datetime
