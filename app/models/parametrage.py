from datetime import datetime
from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Parametrage(Base):
    __tablename__ = "parametrage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    cle: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    valeur: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
