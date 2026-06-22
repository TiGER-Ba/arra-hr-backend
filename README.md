---
title: ARRA HR Platform API
emoji: 🏢
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# ARRA Engineering — HR Platform API

Backend FastAPI de la plateforme RH d'ARRA ENGINEERING SARL (chatbot Groq + RAG HuggingFace, génération PDF, soldes, dépôt documentaire).

Déployé sur HuggingFace Spaces (SDK Docker, port 7860).

## Variables d'environnement (à définir dans Settings → Secrets du Space)

| Variable | Description |
|---|---|
| `DATABASE_URL` | URL PostgreSQL Supabase (`postgresql://...`) |
| `SECRET_KEY` | Clé JWT, 32+ caractères aléatoires |
| `GROQ_API_KEY` | Clé API Groq |
| `GROQ_MODEL` | `llama-3.1-8b-instant` |
| `EMBEDDING_PROVIDER` | `huggingface` |
| `HF_API_KEY` | Token HuggingFace (read) |
| `HF_EMBEDDING_MODEL` | `sentence-transformers/all-MiniLM-L6-v2` |
| `ALLOWED_ORIGINS` | URL du frontend Vercel (ex: `https://mon-app.vercel.app`) |
