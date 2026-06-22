from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.conversation import Conversation
from app.models.employee import Employe
from app.models.message import Message
from app.models.user import Utilisateur
from app.schemas.conversation import ChatRequest, ChatResponse, ConversationOut
from app.services.auth import get_current_user
from app.services.chatbot import process_message

router = APIRouter()


def _get_employe(current_user: Utilisateur, db: Session) -> Employe:
    employe = db.query(Employe).filter(Employe.utilisateur_id == current_user.id).first()
    if not employe:
        raise HTTPException(status_code=404, detail="Profil employé introuvable")
    return employe


@router.post("/start", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
def start_conversation(
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)
    conv = Conversation(employe_id=employe.id)
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


@router.post("/{conversation_id}/message", response_model=ChatResponse)
async def send_message(
    conversation_id: int,
    payload: ChatRequest,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)

    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.employe_id == employe.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable")

    result = await process_message(
        db=db,
        conversation_id=conversation_id,
        employe_id=employe.id,
        user_message=payload.message,
        employe=employe,
    )

    return ChatResponse(
        message=result["message"],
        conversation_id=conversation_id,
        demande_created=result.get("demande_created", False),
        demande_id=result.get("demande_id"),
        type_demande=result.get("type_demande"),
    )


@router.get("/{conversation_id}/messages", response_model=ConversationOut)
def get_messages(
    conversation_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)

    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.employe_id == employe.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    return conv


@router.get("/mes-conversations", response_model=list[ConversationOut])
def list_conversations(
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)
    return (
        db.query(Conversation)
        .filter(Conversation.employe_id == employe.id)
        .order_by(Conversation.debut.desc())
        .all()
    )


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(
    conversation_id: int,
    current_user: Utilisateur = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    employe = _get_employe(current_user, db)
    conv = db.query(Conversation).filter(
        Conversation.id == conversation_id,
        Conversation.employe_id == employe.id,
    ).first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation introuvable")
    db.delete(conv)
    db.commit()
