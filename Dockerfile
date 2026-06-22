FROM python:3.12-slim-bookworm

# Install system dependencies for WeasyPrint (GTK3, Pango, etc.) — as root
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libffi-dev \
    libcairo2 \
    libglib2.0-0 \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# Non-root user (requis par HuggingFace Spaces — UID 1000)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR /home/user/app

# Install Python dependencies
COPY --chown=user requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=user . .

# Create writable runtime directories (owned by user)
RUN mkdir -p uploads/depot uploads/parametrage chroma_db

# HuggingFace Spaces attend le port 7860 ; $PORT permet aussi Render/Railway
EXPOSE 7860

# Run seed (idempotent) then serve
CMD ["sh", "-c", "python seed.py && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
