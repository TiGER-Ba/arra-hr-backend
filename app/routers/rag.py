from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.models.user import Utilisateur
from app.services.auth import require_rh
from app.services.rag import get_rag_service

router = APIRouter()


class RAGQuery(BaseModel):
    query: str
    k: int = 3


@router.post("/search")
def search_rag(
    payload: RAGQuery,
    current_user: Utilisateur = Depends(require_rh),
):
    rag = get_rag_service()
    result = rag.query(payload.query, k=payload.k)
    return {"query": payload.query, "result": result}


@router.post("/index")
def reindex_documents(
    current_user: Utilisateur = Depends(require_rh),
):
    rag = get_rag_service()
    count = rag.reindex()
    return {"message": f"Re-indexation terminée. {count} chunks dans ChromaDB."}
