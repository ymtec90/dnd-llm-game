import pytest
from unittest.mock import AsyncMock, patch
from sqlmodel import Session, SQLModel
from fastapi.testclient import TestClient

from dndllm26.main import app
from dndllm26.db.session import engine
from dndllm26.db.models import Campaign, Hero, Character, Turn
from dndllm26.game.service import get_turns

@pytest.fixture(name="client")
def client_fixture():
    SQLModel.metadata.create_all(engine)
    # Seed a default campaign and characters
    with Session(engine) as session:
        campaign = Campaign(id=1, title="Teste de Combate", setting="Caverna", tone="heroico")
        session.add(campaign)
        # Player character
        char1 = Character(id=1, campaign_id=1, name="Mira Voss", ancestry="Human", character_class="Rogue", backstory="Scout", is_human=True)
        # AI companion character
        char2 = Character(id=2, campaign_id=1, name="Valerie", ancestry="Elfo", character_class="Cleric", backstory="Divine healer", is_human=False)
        session.add(char1)
        session.add(char2)
        session.commit()
    with TestClient(app) as client:
        yield client
    SQLModel.metadata.drop_all(engine)

@pytest.mark.anyio
async def test_combat_turn_and_speaker_separation(client):
    # Mock stream_dm to yield DM narration containing Valerie's action
    mock_tokens = [
        "O goblin ataca! ",
        "Valerie se defende com o escudo. ",
        "[Valerie]: 'Fiquem atrás de mim!' ",
        "Escolhas:\n1. Atacar o goblin.\n2. Fugir para a saída."
    ]
    
    async def mock_stream_dm(*args, **kwargs):
        for token in mock_tokens:
            yield token

    mock_utility_response = {
        "location": "Caverna Escura",
        "objective": "Derrotar o goblin",
        "summary": "Combate contra o goblin iniciado.",
        "choices": ["Atacar o goblin.", "Fugir para a saída."]
    }

    with patch("dndllm26.api.routes.ollama_service.stream_dm", side_effect=mock_stream_dm), \
         patch("dndllm26.game.service.ollama_service.chat_json", new_callable=AsyncMock) as mock_utility:
         
        mock_utility.return_value = mock_utility_response
        
        # Send action
        response = client.post(
            "/api/campaigns/1/actions/stream",
            json={"content": "Eu ataco o goblin com meu arco."}
        )
        
        assert response.status_code == 200
        # Read the event stream response
        content = response.text
        assert "choices_updated" in content
        
        # Verify the turns in the database
        with Session(engine) as session:
            turns = get_turns(session, 1)
            # Find the DM turn
            dm_turns = [t for t in turns if t.speaker == "DM"]
            assert len(dm_turns) > 0
            dm_content = dm_turns[-1].content
            assert "O goblin ataca!" in dm_content
            # Check speaker separation formatting presence
            assert "[Valerie]" in dm_content
