import pytest
import json
from unittest.mock import AsyncMock, patch
from sqlmodel import Session, SQLModel
from fastapi.testclient import TestClient

from dndllm26.main import app
from dndllm26.db.session import engine
from dndllm26.db.models import Campaign, Character, Turn, WorldState, GameSession, CharacterStatus, CampaignMessage
from dndllm26.game.service import get_turns

@pytest.fixture(name="client")
def client_fixture():
    # Setup database metadata
    SQLModel.metadata.create_all(engine)
    
    # Seed base campaign data
    with Session(engine) as session:
        campaign = Campaign(id=10, title="Tumba do Terror", setting="Ruínas Antigas", tone="sombrio")
        session.add(campaign)
        
        # Add characters
        char1 = Character(id=101, campaign_id=10, name="Roderick", ancestry="Human", character_class="Paladin", backstory="Noble knight", is_human=True)
        char2 = Character(id=102, campaign_id=10, name="Valerie", ancestry="Elfo", character_class="Cleric", backstory="Divine healer", is_human=False)
        session.add(char1)
        session.add(char2)
        
        # Add a turn
        turn = Turn(id=50, campaign_id=10, speaker="DM", content="Vocês entram na tumba escura. A porta se fecha atrás de vocês.")
        session.add(turn)
        
        # Add world state
        state = WorldState(id=10, campaign_id=10, current_location="Entrada da Tumba", active_objective="Encontrar uma saída", scene_summary="Porta fechada.", choices_json=json.dumps(["Investigar a porta", "Acender uma tocha"]))
        session.add(state)
        
        session.commit()
        
    with TestClient(app) as client:
        yield client
        
    SQLModel.metadata.drop_all(engine)


@pytest.mark.anyio
async def test_save_load_campaign_session_flow(client):
    # 1. Create a new game session (Save campaign state)
    response = client.post(
        "/api/sessions",
        json={"campaign_id": 10, "name": "Primeiro Save Roderick"}
    )
    assert response.status_code == 200
    session_data = response.json()
    print("SESSION DATA:", session_data)
    assert session_data["name"] == "Primeiro Save Roderick"
    assert session_data["campaign_id"] == 10
    session_id = session_data["id"]
    
    # Check that Characters and Turn history were snapshot into CharacterStatus and CampaignMessage
    from sqlmodel import select
    with Session(engine) as db_sess:
        statuses = db_sess.exec(
            select(CharacterStatus).where(CharacterStatus.game_session_id == session_id)
        ).all()
        assert len(statuses) == 2
        names = {s.name for s in statuses}
        assert "Roderick" in names
        assert "Valerie" in names
        
        messages = db_sess.exec(
            select(CampaignMessage).where(CampaignMessage.game_session_id == session_id)
        ).all()
        assert len(messages) == 1
        assert messages[0].content == "Vocês entram na tumba escura. A porta se fecha atrás de vocês."

    # 2. Get/Load session detail
    response = client.get(f"/api/sessions/{session_id}")
    assert response.status_code == 200
    detail = response.json()
    assert detail["session"]["name"] == "Primeiro Save Roderick"
    assert len(detail["characters"]) == 2
    assert len(detail["turns"]) == 1
    assert "Investigar a porta" in detail["choices"]

    # 3. Simulate playing on the active session (Submit player action)
    mock_tokens = [
        "A tocha revela escritas antigas na parede. ",
        "Escolhas:\n1. Tentar ler as escritas.\n2. Seguir pelo corredor."
    ]
    
    async def mock_stream_dm(*args, **kwargs):
        for token in mock_tokens:
            yield token

    mock_utility_response = {
        "location": "Entrada da Tumba",
        "objective": "Decifrar as escritas",
        "summary": "Escritas antigas reveladas na parede sob luz de tocha.",
        "choices": ["Tentar ler as escritas.", "Seguir pelo corredor."]
    }

    with patch("dndllm26.api.routes.ollama_service.stream_dm", side_effect=mock_stream_dm), \
         patch("dndllm26.game.service.ollama_service.chat_json", new_callable=AsyncMock) as mock_utility:
         
        mock_utility.return_value = mock_utility_response
        
        response = client.post(
            f"/api/sessions/{session_id}/actions/stream",
            json={"content": "Eu converso com Valerie sobre a nossa missão."}
        )
        assert response.status_code == 200
        content = response.text
        assert "choices_updated" in content

    # Verify that the new player interaction and DM responses were automatically appended to this session's messages
    with Session(engine) as db_sess:
        messages = db_sess.exec(
            select(CampaignMessage)
            .where(CampaignMessage.game_session_id == session_id)
            .order_by(CampaignMessage.created_at)
        ).all()
        # Initial turn + Player Action + DM Response = 3 messages
        assert len(messages) == 3
        assert messages[1].speaker == "Player"
        assert messages[1].content == "Eu converso com Valerie sobre a nossa missão."
        assert messages[2].speaker == "DM"
        assert "A tocha revela escritas" in messages[2].content

    # 4. List all saved sessions
    response = client.get("/api/sessions")
    assert response.status_code == 200
    sessions_list = response.json()
    assert len(sessions_list) >= 1
    assert sessions_list[0]["id"] == session_id

    # 5. Delete session
    response = client.delete(f"/api/sessions/{session_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    
    # Ensure cascade deleted all session-specific tables
    with Session(engine) as db_sess:
        assert db_sess.get(GameSession, session_id) is None
        assert len(db_sess.exec(select(CharacterStatus).where(CharacterStatus.game_session_id == session_id)).all()) == 0
        assert len(db_sess.exec(select(CampaignMessage).where(CampaignMessage.game_session_id == session_id)).all()) == 0
