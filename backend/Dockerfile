# Dockerfile for Backend (FastAPI)
FROM python:3.11-slim

WORKDIR /app

# Install system utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy build config and install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy backend codebase
COPY backend/dndllm26 /app/dndllm26

# Ensure data directory exists
RUN mkdir -p /app/data

# Environment settings
ENV PYTHONUNBUFFERED=1
ENV DATABASE_URL=sqlite:////app/data/dndllm26.db
ENV OLLAMA_HOST=http://host.docker.internal:11434

EXPOSE 8765

# Start FastAPI server
CMD ["uvicorn", "dndllm26.main:app", "--host", "0.0.0.0", "--port", "8765"]
