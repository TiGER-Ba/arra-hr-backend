from datetime import datetime
from pydantic import BaseModel, ConfigDict


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    contenu: str
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employe_id: int
    titre: str | None = None
    debut: datetime
    statut: str
    messages: list[MessageOut] = []


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    message: str
    conversation_id: int
    demande_created: bool = False
    demande_id: int | None = None
    type_demande: str | None = None
