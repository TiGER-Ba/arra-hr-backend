from datetime import datetime
from pydantic import BaseModel, ConfigDict


class SoldeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    employe_id: int
    type: str
    unite: str
    quota_total: float
    consomme: float
    annee_reference: int
    updated_at: datetime


class SoldeDetail(SoldeOut):
    label: str | None = None
    reste: float = 0


class SoldeCreate(BaseModel):
    type: str
    quota_total: float
    annee_reference: int | None = None


class SoldeAjuster(BaseModel):
    delta: float
    motif: str


class MouvementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    solde_id: int
    delta: float
    motif: str
    demande_id: int | None
    cree_par_rh_id: int | None
    created_at: datetime
