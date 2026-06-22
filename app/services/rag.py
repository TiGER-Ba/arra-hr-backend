import os
import shutil
from pathlib import Path

from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings

_rag_instance: "RAGService | None" = None


def _create_embeddings():
    provider = settings.EMBEDDING_PROVIDER.lower()

    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEndpointEmbeddings
        if not settings.HF_API_KEY:
            raise RuntimeError(
                "HF_API_KEY est requis pour EMBEDDING_PROVIDER=huggingface. "
                "Créez un token gratuit sur https://huggingface.co/settings/tokens"
            )
        return HuggingFaceEndpointEmbeddings(
            model=settings.HF_EMBEDDING_MODEL,
            huggingfacehub_api_token=settings.HF_API_KEY,
        )

    # Fallback: Ollama (local)
    from langchain_ollama import OllamaEmbeddings
    return OllamaEmbeddings(
        model=settings.OLLAMA_EMBEDDING_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
    )


def _extract_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".txt":
        return path.read_text(encoding="utf-8", errors="ignore")
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            return "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:
            print(f"[RAG] Erreur PDF {path.name} : {e}")
            return ""
    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            print(f"[RAG] Erreur DOCX {path.name} : {e}")
            return ""
    return ""


class RAGService:
    def __init__(self):
        self.embeddings = _create_embeddings()
        self.vectorstore = self._init_vectorstore()

    def _init_vectorstore(self) -> Chroma:
        chroma_dir = settings.CHROMA_DIR
        docs_dir = Path(settings.DOCUMENTS_RH_DIR)

        if os.path.exists(chroma_dir):
            try:
                store = Chroma(persist_directory=chroma_dir, embedding_function=self.embeddings)
                count = store._collection.count()
                if count > 0:
                    # Test a query to verify embedding dimensions match
                    store.similarity_search("test", k=1)
                print(f"[RAG] ChromaDB chargée : {count} chunks existants.")
                return store
            except Exception as e:
                print(f"[RAG] Échec chargement ChromaDB ({e}), suppression et réindexation…")
                try:
                    import shutil
                    shutil.rmtree(chroma_dir, ignore_errors=True)
                except Exception:
                    pass

        if not docs_dir.exists():
            print(f"[RAG] Dossier documents_rh introuvable : {docs_dir}")
            return Chroma(persist_directory=chroma_dir, embedding_function=self.embeddings)

        print("[RAG] Première indexation des documents RH dans ChromaDB...")
        return self._build_store(docs_dir, chroma_dir)

    def _collect_chunks(self, docs_dir: Path) -> list:
        splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
        all_chunks = []
        if not docs_dir.exists():
            return all_chunks
        for f in sorted(docs_dir.iterdir()):
            if f.suffix.lower() not in (".txt", ".pdf", ".docx"):
                continue
            text = _extract_text(f)
            if not text.strip():
                print(f"[RAG] {f.name} : texte vide, ignoré.")
                continue
            chunks = splitter.create_documents([text], metadatas=[{"source": f.name}])
            all_chunks.extend(chunks)
            print(f"[RAG] {f.name} -> {len(chunks)} chunks")
        return all_chunks

    def _build_store(self, docs_dir: Path, chroma_dir: str) -> Chroma:
        all_chunks = self._collect_chunks(docs_dir)
        if not all_chunks:
            print("[RAG] Aucun document à indexer.")
            return Chroma(persist_directory=chroma_dir, embedding_function=self.embeddings)
        store = Chroma.from_documents(
            documents=all_chunks,
            embedding=self.embeddings,
            persist_directory=chroma_dir,
        )
        print(f"[RAG] {len(all_chunks)} chunks indexés.")
        return store

    def query(self, text: str, k: int = 5) -> str:
        try:
            results = self.vectorstore.similarity_search(text, k=k)
            if not results:
                return ""
            parts = []
            for doc in results:
                source = doc.metadata.get("source", "document inconnu")
                parts.append(f"[Source: {source}]\n{doc.page_content}")
            return "\n\n".join(parts)
        except Exception as e:
            print(f"[RAG] Erreur de recherche : {e}")
            return ""

    def reindex(self) -> int:
        docs_dir = Path(settings.DOCUMENTS_RH_DIR)
        chroma_dir = settings.CHROMA_DIR

        if self.vectorstore is None:
            self.vectorstore = Chroma(persist_directory=chroma_dir, embedding_function=self.embeddings)

        try:
            existing = self.vectorstore.get()
            existing_ids = existing.get("ids", []) if isinstance(existing, dict) else []
            if existing_ids:
                self.vectorstore.delete(ids=existing_ids)
                print(f"[RAG] {len(existing_ids)} anciens chunks supprimés de la collection.")
        except Exception as e:
            print(f"[RAG] Avertissement purge collection : {e}")

        all_chunks = self._collect_chunks(docs_dir)

        if all_chunks:
            try:
                self.vectorstore.add_documents(all_chunks)
                print(f"[RAG] {len(all_chunks)} nouveaux chunks indexés.")
            except Exception as e:
                print(f"[RAG] Erreur indexation : {e}")
                raise

        try:
            count = self.vectorstore._collection.count()
        except Exception:
            count = 0
        return count


def get_rag_service() -> RAGService:
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = RAGService()
    return _rag_instance


def reset_rag_service():
    global _rag_instance
    _rag_instance = None
