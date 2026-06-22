from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Demande(Base):
    __tablename__ = "demandes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(Integer, ForeignKey("employes.id"), nullable=False, index=True)
    conversation_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("conversations.id"), nullable=True)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    statut: Mapped[str] = mapped_column(String(30), default="en_attente", index=True)
    donnees_collectees: Mapped[dict] = mapped_column(JSON, default=dict)
    raison_rejet: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    employe: Mapped["Employe"] = relationship("Employe", back_populates="demandes")
    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="demandes")
    documents: Mapped[list["Document"]] = relationship("Document", back_populates="demande")
