from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Departement(Base):
    """Référentiel des départements proposés à la saisie d'une fiche salarié.

    Le département reste stocké en texte sur `Employe` : cette table sert de
    liste de choix, pas de clé étrangère. Renommer ou retirer une entrée ici
    ne touche donc aucune fiche existante — c'est voulu, les documents déjà
    émis portent le libellé tel qu'il était à leur génération.
    """

    __tablename__ = "departements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    nom: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
