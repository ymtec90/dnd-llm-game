import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from pathlib import Path

from dndllm26.main import app
from dndllm26.rag.store import rag_store
from dndllm26.db.session import engine
from sqlmodel import Session, SQLModel

@pytest.fixture(name="client")
def client_fixture():
    # Setup clean sqlite in-memory db for testing
    SQLModel.metadata.create_all(engine)
    with TestClient(app) as client:
        yield client
    SQLModel.metadata.drop_all(engine)

@pytest.mark.anyio
async def test_rag_embedding_generation():
    # Mock the ollama_service.embed call
    mock_vector = [0.1] * 768
    with patch("dndllm26.rag.store.ollama_service.embed", new_callable=AsyncMock) as mock_embed:
        mock_embed.return_value = mock_vector
        
        # Create a temp txt file
        temp_file = Path("test_adventure.txt")
        temp_file.write_text("Esta é uma aventura teste para o RAG. O dragão dorme no topo da montanha.", encoding="utf-8")
        
        try:
            chunks_count = await rag_store.index_document(temp_file, document_id=999)
            assert chunks_count > 0
            assert mock_embed.called
            
            # Search
            results = await rag_store.search("dragão", limit=1, document_ids=[999])
            assert len(results) == 1
            assert "dragão" in results[0]["text"]
        finally:
            if temp_file.exists():
                temp_file.unlink()

def test_upload_endpoints(client):
    # Mock index_lore_document task to avoid actual background execution calling RAG/Ollama in test
    with patch("dndllm26.api.routes.index_lore_document") as mock_index_task:
        # Test PDF upload rejection if not PDF/TXT
        response = client.post(
            "/api/lore/upload",
            files={"file": ("test.png", b"fake binary data", "image/png")}
        )
        assert response.status_code == 400
        
        # Test TXT upload success
        response = client.post(
            "/api/lore/upload",
            files={"file": ("test.txt", b"Aventura no deserto", "text/plain")}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "test.txt"
        assert data["status"] == "queued"
