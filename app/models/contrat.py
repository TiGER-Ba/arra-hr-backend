from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Contrat(Base):
    """Registre des contrats émis, et source du numéro officiel.

    Le numéro suit la forme « 0045-2026 » : un compteur **partagé par tous les
    types**, remis à 1 chaque année — c'est ce que montrent les contrats
    existants, où un contrat de prestation porte 0045 et un auto-entrepreneur
    0046 la même année.

    ⚠️ Le numéro est attribué UNE FOIS par (salarié, type de contrat) et ne
    bouge plus : régénérer le PDF réimprime le même contrat, il ne crée pas un
    nouvel engagement. Un changement de type (CDD → CDI) est en revanche un
    contrat distinct, donc un nouveau numéro.
    """

    __tablename__ = "contrats"
    __table_args__ = (
        UniqueConstraint("annee", "sequence", name="uq_contrat_annee_sequence"),
        UniqueConstraint("employe_id", "type_contrat", name="uq_contrat_employe_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    numero: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    annee: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)

    employe_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("employes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type_contrat: Mapped[str] = mapped_column(String(20), nullable=False)

    cree_le: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    cree_par_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("utilisateurs.id", ondelete="SET NULL"), nullable=True
    )

    employe: Mapped["Employe"] = relationship("Employe")
