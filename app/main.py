import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import auth, chat, demandes, depot, documents, knowledge, notifications, rag, rh, soldes, users


def _run_startup_migrations():
    """Migrations légères idempotentes (create_all n'altère pas les tables existantes).

    Ajoute les colonnes manquantes sans Alembic — compatible PostgreSQL et SQLite.
    """
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    try:
        cols = {c["name"] for c in insp.get_columns("utilisateurs")}
    except Exception:
        return  # table pas encore créée : create_all s'en charge avec le bon schéma
    if "prenom" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE utilisateurs ADD COLUMN prenom VARCHAR(100)"))
        print("[migrate] utilisateurs.prenom ajouté")


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.models  # noqa: F401 — ensure all models are registered with Base

    Base.metadata.create_all(bind=engine)
    _run_startup_migrations()
    os.makedirs("uploads", exist_ok=True)
    os.makedirs(os.path.join("uploads", "depot"), exist_ok=True)
    os.makedirs(os.path.join("uploads", "parametrage"), exist_ok=True)

    try:
        from app.services.rag import get_rag_service
        get_rag_service()
    except Exception as e:
        print(f"[WARNING] RAG init failed (app continues without RAG): {e}")

    yield


app = FastAPI(
    title="HR Platform API",
    version="3.1.0",
    description="Plateforme RH avec chatbot Groq + RAG HuggingFace embeddings",
    lifespan=lifespan,
)

# CORS — accept localhost (dev) + any Vercel/Render deployment
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(demandes.router, prefix="/api/demandes", tags=["demandes"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(depot.router, prefix="/api/depot", tags=["depot"])
app.include_router(soldes.router, prefix="/api/soldes", tags=["soldes"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(rh.router, prefix="/api/rh", tags=["rh"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(rag.router, prefix="/api/rag", tags=["rag"])
app.include_router(knowledge.router, prefix="/api/knowledge", tags=["knowledge"])

# ⚠️ SÉCURITÉ : plus AUCUN dossier `uploads/` n'est exposé publiquement.
# Les documents générés (attestations, salaires) et le dépôt (bulletins, contrats)
# sont servis UNIQUEMENT via des routes protégées par JWT + contrôle de propriété
# (documents.py::download_document, depot.py::telecharger_document).
# La signature/cachet sont embarqués en base64 (aperçu RH + PDF WeasyPrint).


@app.get("/")
def root():
    return {"message": "HR Platform API v3.1 — Groq + HuggingFace embeddings", "version": "3.1.0"}
