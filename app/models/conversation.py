from datetime import datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    employe_id: Mapped[int] = mapped_column(Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False)
    titre: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debut: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    fin: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    statut: Mapped[str] = mapped_column(String(20), default="active")  # active | terminee

    employe: Mapped["Employe"] = relationship("Employe", back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship("Message", back_populates="conversation", order_by="Message.created_at", cascade="all, delete-orphan", passive_deletes=True)
    demandes: Mapped[list["Demande"]] = relationship("Demande", back_populates="conversation")
