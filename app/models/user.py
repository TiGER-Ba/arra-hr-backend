from datetime import datetime
from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Utilisateur(Base):
    __tablename__ = "utilisateurs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    prenom: Mapped[str | None] = mapped_column(String(100), nullable=True)
    email: Mapped[str] = mapped_column(String(150), unique=True, nullable=False, index=True)
    mot_de_passe: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # employe | rh | admin
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Invitation par email : jeton à usage unique pour définir soi-même son mot de passe
    invite_token: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    invite_token_expire: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    employe: Mapped["Employe"] = relationship("Employe", back_populates="utilisateur", uselist=False)
    rh: Mapped["RH"] = relationship("RH", back_populates="utilisateur", uselist=False)
    notifications: Mapped[list["Notification"]] = relationship("Notification", back_populates="utilisateur", cascade="all, delete-orphan")
