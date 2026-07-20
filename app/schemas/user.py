from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr


class UtilisateurCreate(BaseModel):
    nom: str
    email: EmailStr
    mot_de_passe: str
    role: str  # employe | rh | admin


class UtilisateurLogin(BaseModel):
    email: EmailStr
    mot_de_passe: str


class EmployeCreate(BaseModel):
    matricule: str
    poste: str
    departement: str
    salaire_base: float
    date_embauche: str  # format: YYYY-MM-DD
    type_contrat: str | None = "CDI"
    cin: str | None = None
    cnss: str | None = None
    adresse: str | None = None
    telephone: str | None = None
    service_rh: str | None = None  # utilisé si role == rh


class UtilisateurOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nom: str
    email: str
    role: str
    is_active: bool
    employe_id: int | None = None  # présent si le compte est aussi un salarié
    created_at: datetime


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UtilisateurOut


class TokenData(BaseModel):
    user_id: int | None = None
    role: str | None = None
