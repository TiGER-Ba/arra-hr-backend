from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File

from app.config import settings
from app.models.user import Utilisateur
from app.services.auth import require_rh
from app.services.rag import get_rag_service
from app.services.security import read_upload_limited, safe_filename, safe_join

router = APIRouter()

DOCS_DIR = Path(settings.DOCUMENTS_RH_DIR)
ALLOWED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if ext == ".docx":
        from docx import Document
        doc = Document(str(path))
        return "\n".join(p.text for p in doc.paragraphs)
    return ""


@router.get("/")
def list_documents(current_user: Utilisateur = Depends(require_rh)):
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for f in sorted(DOCS_DIR.iterdir()):
        if f.suffix.lower() in ALLOWED_EXTENSIONS:
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "ext": f.suffix.lower().lstrip("."),
            })
    return files


@router.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    current_user: Utilisateur = Depends(require_rh),
):
    # ⚠️ SÉCURITÉ : le nom fourni par le client ne doit JAMAIS servir tel quel à
    # construire un chemin (« ../.. » → écriture hors du dossier). On assainit,
    # et on plafonne la taille avant d'écrire quoi que ce soit sur le disque.
    nom = safe_filename(file.filename)
    ext = Path(nom).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Format non supporté. Acceptés : .txt, .pdf, .docx")

    contenu = read_upload_limited(file, settings.MAX_UPLOAD_MB, ALLOWED_EXTENSIONS)

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    dest = Path(safe_join(str(DOCS_DIR), nom))
    dest.write_bytes(contenu)

    # Validate we can extract text
    try:
        text = _extract_text(dest)
        if not text.strip():
            dest.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail="Le fichier ne contient pas de texte lisible")
    except HTTPException:
        raise
    except Exception as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Erreur de lecture : {e}")

    # Reindex ChromaDB
    rag = get_rag_service()
    count = rag.reindex()

    return {"message": f"'{file.filename}' ajouté et indexé ({count} chunks total)"}


@router.delete("/{filename}")
def delete_document(
    filename: str,
    current_user: Utilisateur = Depends(require_rh),
):
    # Sécurité : on refuse les chemins relatifs/absolus
    safe_name = safe_filename(filename)
    path = Path(safe_join(str(DOCS_DIR), safe_name))
    if not path.exists() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=404, detail="Fichier introuvable")
    try:
        path.unlink()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Impossible de supprimer le fichier : {e}")

    try:
        rag = get_rag_service()
        count = rag.reindex()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Fichier supprimé mais la réindexation a échoué : {e}")

    return {"message": f"'{safe_name}' supprimé et base ré-indexée ({count} chunks total)"}


@router.post("/reindex")
def reindex(current_user: Utilisateur = Depends(require_rh)):
    try:
        rag = get_rag_service()
        count = rag.reindex()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur de réindexation : {e}")
    return {"message": f"Ré-indexation terminée — {count} chunks dans ChromaDB"}
