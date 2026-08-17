import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import audit, auth, chat, crm, demandes, depot, documents, knowledge, notifications, pointage, rag, rh, soldes, users


@asynccontextmanager
async def lifespan(app: FastAPI):
    import app.models  # noqa: F401 — ensure all models are registered with Base
    from app.config import settings
    from app.db_migrate import run_migrations
    from app.services.security import verifier_configuration

    # ⚠️ Contrôle de configuration AVANT tout : en production, un SECRET_KEY par
    # défaut rendrait les JWT falsifiables → on refuse de démarrer.
    for avertissement in verifier_configuration(settings):
        print(f"[WARNING] {avertissement}")

    Base.metadata.create_all(bind=engine)
    run_migrations()
    os.makedirs("uploads", exist_ok=True)
    os.makedirs(os.path.join("uploads", "depot"), exist_ok=True)
    os.makedirs(os.path.join("uploads", "parametrage"), exist_ok=True)

    # ⚠️ Init du RAG en ARRIÈRE-PLAN : ne JAMAIS bloquer le démarrage.
    # uvicorn n'accepte les requêtes qu'après la fin du lifespan ; or l'indexation
    # ChromaDB (embeddings via API HF) peut prendre du temps après un rebuild (dossier
    # vide) → le health check HF échouerait → redémarrages en boucle. Le RAG se
    # réchauffe donc dans un thread ; la 1re requête chatbot l'initialisera au besoin.
    import threading

    def _warm_rag():
        try:
            from app.services.rag import get_rag_service
            get_rag_service()
            print("[startup] RAG prêt.")
        except Exception as e:
            print(f"[WARNING] RAG init failed (app continues without RAG): {e}")

    threading.Thread(target=_warm_rag, daemon=True).start()

    yield


app = FastAPI(
    title="HR Platform API",
    version="3.1.0",
    description="Plateforme RH avec chatbot Groq + RAG HuggingFace embeddings",
    lifespan=lifespan,
)

# CORS — liste blanche d'origines exactes (jamais « * » avec allow_credentials).
ALLOWED_ORIGINS = [o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:3000").split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def entetes_securite(request, call_next):
    """En-têtes de sécurité sur toutes les réponses (défense en profondeur)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-XSS-Protection", "0")
    return response

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(demandes.router, prefix="/api/demandes", tags=["demandes"])
app.include_router(documents.router, prefix="/api/documents", tags=["documents"])
app.include_router(depot.router, prefix="/api/depot", tags=["depot"])
app.include_router(soldes.router, prefix="/api/soldes", tags=["soldes"])
app.include_router(pointage.router, prefix="/api/pointage", tags=["pointage"])
app.include_router(crm.router, prefix="/api/crm", tags=["crm"])
app.include_router(notifications.router, prefix="/api/notifications", tags=["notifications"])
app.include_router(rh.router, prefix="/api/rh", tags=["rh"])
app.include_router(users.router, prefix="/api/users", tags=["users"])
app.include_router(audit.router, prefix="/api/audit", tags=["audit"])
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
