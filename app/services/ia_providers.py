"""Fiabilité des fournisseurs IA : découverte des modèles et repli automatique.

Problème résolu : Groq et HuggingFace retirent régulièrement des modèles. Quand
le modèle configuré disparaît, le chatbot renvoyait une erreur brute à chaque
message. Ici on :
  1. interroge le fournisseur pour connaître les modèles RÉELLEMENT disponibles,
  2. bascule automatiquement sur un modèle équivalent si celui configuré a disparu,
  3. mémorise le repli en base pour que l'administrateur le voie.
"""
import httpx
from sqlalchemy.orm import Session

from app.services.parametrage import get_param, set_param

GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"

# Ordre de préférence pour le repli (du plus adapté au plus générique).
# On ne se limite pas à cette liste : elle sert uniquement à choisir intelligemment
# parmi les modèles réellement renvoyés par l'API.
GROQ_PREFERENCES = [
    "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
]

# Modèles d'embedding connus pour fonctionner avec l'API d'inférence HuggingFace.
HF_EMBEDDING_SUGGESTIONS = [
    {"id": "sentence-transformers/all-MiniLM-L6-v2", "note": "Rapide et léger — recommandé"},
    {"id": "sentence-transformers/all-mpnet-base-v2", "note": "Plus précis, plus lent"},
    {"id": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2", "note": "Multilingue (FR)"},
    {"id": "intfloat/multilingual-e5-base", "note": "Multilingue, bonne qualité"},
    {"id": "BAAI/bge-m3", "note": "Multilingue, longue portée"},
]


# ─── Groq ────────────────────────────────────────────────────────────────────

def lister_modeles_groq(api_key: str, timeout: float = 15.0) -> list[dict]:
    """Modèles disponibles pour cette clé. Lève ValueError avec un message clair."""
    if not api_key:
        raise ValueError("Aucune clé Groq fournie")
    try:
        resp = httpx.get(
            GROQ_MODELS_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        raise ValueError(f"Contact impossible avec Groq : {e}")

    if resp.status_code == 401:
        raise ValueError("Clé Groq invalide ou révoquée")
    if resp.status_code == 429:
        raise ValueError("Quota Groq dépassé — réessayez plus tard")
    if resp.status_code >= 400:
        raise ValueError(f"Groq a répondu {resp.status_code}")

    data = resp.json().get("data", [])
    modeles = []
    for m in data:
        mid = m.get("id")
        if not mid:
            continue
        # On écarte les modèles non conversationnels (audio, vision-only, garde-fous)
        if any(x in mid.lower() for x in ("whisper", "tts", "guard")):
            continue
        modeles.append({
            "id": mid,
            "owned_by": m.get("owned_by", ""),
            "context_window": m.get("context_window"),
            "active": m.get("active", True),
        })
    modeles.sort(key=lambda x: x["id"])
    return modeles


def choisir_modele_de_repli(disponibles: list[str]) -> str | None:
    """Meilleur modèle de remplacement parmi ceux réellement disponibles."""
    for pref in GROQ_PREFERENCES:
        if pref in disponibles:
            return pref
    # Sinon : un modèle « instant »/« versatile » plausible, sinon le premier venu
    for mid in disponibles:
        if "instant" in mid or "versatile" in mid:
            return mid
    return disponibles[0] if disponibles else None


def resoudre_modele_groq(db: Session, api_key: str, modele_voulu: str) -> tuple[str, str | None]:
    """Renvoie (modèle_à_utiliser, avertissement).

    Si `modele_voulu` n'existe plus chez Groq, bascule sur un équivalent et
    enregistre le changement (clé `groq_model`) pour que l'admin le constate.
    Toute erreur réseau est non bloquante : on garde le modèle demandé.
    """
    try:
        modeles = lister_modeles_groq(api_key)
    except ValueError:
        return modele_voulu, None  # hors ligne / quota : ne pas bloquer l'utilisateur

    ids = [m["id"] for m in modeles]
    if not ids or modele_voulu in ids:
        return modele_voulu, None

    repli = choisir_modele_de_repli(ids)
    if not repli:
        return modele_voulu, None

    set_param(db, "groq_model", repli)
    set_param(db, "groq_model_repli_depuis", modele_voulu)
    db.commit()
    return repli, (
        f"Le modèle « {modele_voulu} » n'est plus proposé par Groq. "
        f"Bascule automatique sur « {repli} »."
    )


# ─── HuggingFace ─────────────────────────────────────────────────────────────

def tester_embedding_hf(api_key: str, model: str, timeout: float = 30.0) -> dict:
    """Vérifie qu'un modèle d'embedding répond vraiment (et renvoie sa dimension)."""
    if not api_key:
        raise ValueError("Aucune clé HuggingFace fournie")
    if not model:
        raise ValueError("Aucun modèle d'embedding indiqué")

    try:
        from langchain_huggingface import HuggingFaceEndpointEmbeddings
        embeddings = HuggingFaceEndpointEmbeddings(model=model, huggingfacehub_api_token=api_key)
        vecteur = embeddings.embed_query("test de disponibilité")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        if "401" in msg or "authorization" in msg.lower():
            raise ValueError("Clé HuggingFace invalide ou révoquée")
        if "404" in msg:
            raise ValueError(f"Le modèle « {model} » est introuvable ou n'est plus servi par l'API")
        if "503" in msg or "loading" in msg.lower():
            raise ValueError(f"Le modèle « {model} » est en cours de chargement — réessayez dans un instant")
        raise ValueError(f"Échec du test d'embedding : {msg[:200]}")

    if not vecteur:
        raise ValueError("Le modèle n'a renvoyé aucun vecteur")
    return {"model": model, "dimension": len(vecteur)}


def etat_ia(db: Session) -> dict:
    """Diagnostic affiché dans /rh/parametrage (sans jamais exposer les clés)."""
    return {
        "groq_model": get_param(db, "groq_model", ""),
        "repli_depuis": get_param(db, "groq_model_repli_depuis", ""),
    }
