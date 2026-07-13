"""
Paramétrage IA centralisé (table `parametrage`, clé/valeur) — comme la plateforme
de recrutement. Les valeurs vivent en base et sont modifiables depuis /rh/parametrage.
Fallback systématique sur le .env (settings) si la clé n'est pas encore en base.
"""
from sqlalchemy.orm import Session

from app.config import settings
from app.models.parametrage import Parametrage

# Clés IA gérées (préfixes utilisés pour l'affichage / la détection embeddings)
IA_KEYS = [
    "groq_api_key", "groq_api_key_2", "groq_model",
    "embedding_provider", "hf_api_key", "hf_embedding_model",
    "ollama_base_url", "ollama_embedding_model",
]


def get_param(db: Session, cle: str, default: str = "") -> str:
    row = db.query(Parametrage).filter(Parametrage.cle == cle).first()
    return (row.valeur if row and row.valeur else "") or default


def set_param(db: Session, cle: str, valeur: str) -> None:
    row = db.query(Parametrage).filter(Parametrage.cle == cle).first()
    if row:
        row.valeur = valeur
    else:
        db.add(Parametrage(cle=cle, valeur=valeur))


# ── Groq ────────────────────────────────────────────────────────────────────

def groq_keys(db: Session) -> list[str]:
    """Clés Groq (principale + secours), fallback .env, non vides et dédupliquées."""
    raw = [get_param(db, "groq_api_key", settings.GROQ_API_KEY),
           get_param(db, "groq_api_key_2", "")]
    out, seen = [], set()
    for k in raw:
        k = (k or "").strip()
        if k and k not in seen:
            out.append(k)
            seen.add(k)
    return out


def groq_model(db: Session) -> str:
    return get_param(db, "groq_model", settings.GROQ_MODEL) or settings.GROQ_MODEL


# ── Embeddings (RAG) ──────────────────────────────────────────────────────────

def embedding_config(db: Session) -> dict:
    return {
        "provider": (get_param(db, "embedding_provider", settings.EMBEDDING_PROVIDER) or "huggingface"),
        "hf_api_key": get_param(db, "hf_api_key", settings.HF_API_KEY),
        "hf_model": get_param(db, "hf_embedding_model", settings.HF_EMBEDDING_MODEL),
        "ollama_base_url": get_param(db, "ollama_base_url", settings.OLLAMA_BASE_URL),
        "ollama_model": get_param(db, "ollama_embedding_model", settings.OLLAMA_EMBEDDING_MODEL),
    }
