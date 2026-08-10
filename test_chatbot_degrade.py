"""Vérifie que le chatbot reste utilisable quand la base de connaissances (RAG)
est indisponible, et qu'une panne du modèle renvoie un message lisible plutôt
qu'une erreur 500.
"""
import asyncio
import sys
import types

import app.services.chatbot as cb

ok = True


class FakeDB:
    def add(self, *_a, **_k): pass
    def commit(self): pass
    def query(self, *_a, **_k): return self
    def filter(self, *_a, **_k): return self
    def order_by(self, *_a, **_k): return self
    def all(self): return []
    def first(self): return None


# ── 1. RAG en panne : le chatbot doit répondre quand même ────────────────────
print("— RAG indisponible (pas de clé HuggingFace) —")


def rag_ko():
    raise RuntimeError("Clé HuggingFace requise pour le provider 'huggingface'")


cb.get_rag_service = rag_ko
cb._build_system_prompt = lambda db, ctx, employe=None: f"PROMPT(contexte={ctx!r})"
cb.groq_keys = lambda db: ["cle-de-test"]
cb.groq_model = lambda db: "modele-test"
cb._invoke_with_rotation = lambda keys, model, msgs, db=None: "Il vous reste 12 jours de congés."

res = asyncio.run(cb.process_message(FakeDB(), 1, 1, "combien de jours de conges ?"))
if res["message"] == "Il vous reste 12 jours de congés.":
    print("  OK    le chatbot répond malgré l'absence de base de connaissances")
else:
    ok = False
    print(f"  ECHEC {res}")

# ── 2. Aucune clé Groq : message explicite, pas de plantage ──────────────────
print("\n— Aucune clé Groq configurée —")
cb.groq_keys = lambda db: []
cb.settings = types.SimpleNamespace(GROQ_API_KEY="")
res = asyncio.run(cb.process_message(FakeDB(), 1, 1, "bonjour"))
if "pas encore configuré" in res["message"]:
    print("  OK    message explicite pour l'administrateur")
else:
    ok = False
    print(f"  ECHEC {res}")

# ── 3. Quota dépassé : message compréhensible ────────────────────────────────
print("\n— Quota du modèle dépassé —")
cb.groq_keys = lambda db: ["cle-de-test"]


def quota_depasse(*_a, **_k):
    raise RuntimeError("Error code: 429 - rate limit exceeded")


cb._invoke_with_rotation = quota_depasse
res = asyncio.run(cb.process_message(FakeDB(), 1, 1, "bonjour"))
if "limite d'utilisation" in res["message"]:
    print("  OK    message de quota lisible")
else:
    ok = False
    print(f"  ECHEC {res}")

# ── 4. Clé invalide ─────────────────────────────────────────────────────────
print("\n— Clé du modèle invalide —")


def cle_invalide(*_a, **_k):
    raise RuntimeError("Error code: 401 - Invalid API Key")


cb._invoke_with_rotation = cle_invalide
res = asyncio.run(cb.process_message(FakeDB(), 1, 1, "bonjour"))
if "invalide ou expirée" in res["message"]:
    print("  OK    message de clé invalide lisible")
else:
    ok = False
    print(f"  ECHEC {res}")

print("\nRESULTAT :", "CHATBOT ROBUSTE" if ok else "DES CONTROLES ONT ECHOUE")
sys.exit(0 if ok else 1)
