from sqlalchemy import Boolean, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    type: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    nom: Mapped[str] = mapped_column(String(100), nullable=False)
    contenu_html: Mapped[str] = mapped_column(Text, nullable=False)
    champs_requis: Mapped[list] = mapped_column(JSON, default=list)
    actif: Mapped[bool] = mapped_column(Boolean, default=True)
    # True = modifié manuellement : le seed ne l'écrase plus lors des mises à jour
    personnalise: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    documents: Mapped[list["Document"]] = relationship("Document", back_populates="template")
