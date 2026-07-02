import pytest
import json
from unittest.mock import AsyncMock, patch
from sqlmodel import Session, SQLModel, select
from fastapi.testclient import TestClient

from dndllm26.main import app
from dndllm26.db.session import engine
from dndllm26.db.models import Campaign, Character, WorldState, GameSession, CharacterStatus, CampaignMessage
from dndllm26.game.service import extract_status_updates

@pytest.fixture(name="client")
def client_fixture():
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        campaign = Campaign(id=20, title="Torre da Magia", setting="Torre", tone="fantasia")
        session.add(campaign)
        
        # Add character status
        char = Character(id=201, campaign_id=20, name="Kael", ancestry="Elfo", character_class="Mago", backstory="Sábio", is_human=True)
        session.add(char)
        session.commit()
        
        game_session = GameSession(id=5, campaign_id=20, name="Sessão Inventário")
        session.add(game_session)
        session.commit()
        
        status = CharacterStatus(
            id=301,
            game_session_id=5,
            character_id=201,
            name="Kael",
            ancestry="Elfo",
            character_class="Mago",
            backstory="Sábio",
            inventory=json.dumps([
                {"id": "pot1", "name": "Poção de Cura Menor", "type": "consumable", "effect": "cura 6 HP"},
                {"id": "swd1", "name": "Cajado do Fogo", "type": "weapon", "effect": "lança bolas de fogo"}
            ]),
            is_human=True,
            hp=4,
            max_hp=10,
            level=1,
            xp=80,
            gold=15
        )
        session.add(status)
        session.commit()
        
    with TestClient(app) as client:
        yield client
    SQLModel.metadata.drop_all(engine)


@pytest.mark.anyio
async def test_extract_status_updates_and_level_up(client):
    # Test LLM status extraction
    mock_llm_json = {
        "character_updates": [
            {
                "name": "Kael",
                "xp_gained": 30, # 80 + 30 = 110 (triggers level up, needs 100)
                "gold_gained": 25,
                "hp_change": -2,
                "items_added": [
                    {"name": "Pergaminho de Escudo", "type": "consumable", "effect": "bloqueia ataques"}
                ],
                "items_removed": ["Cajado do Fogo"]
            }
        ]
    }
    
    with patch("dndllm26.game.service.ollama_service.chat_json", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = mock_llm_json
        
        with Session(engine) as session:
            updates = await extract_status_updates(session, campaign_id=20, dm_text="Kael derrota o goblin e acha ouro.", game_session_id=5)
            
            assert len(updates) == 1
            up = updates[0]
            assert up["name"] == "Kael"
            assert up["xp_gained"] == 30
            assert up["gold_gained"] == 25
            assert up["hp_change"] == -2
            assert up["level_up"] is True
            assert up["level"] == 2 # 1 -> 2
            
            # Check updated database record
            status = session.exec(
                select(CharacterStatus)
                .where(CharacterStatus.game_session_id == 5)
                .where(CharacterStatus.character_id == 201)
            ).first()
            
            assert status.level == 2
            assert status.xp == 10 # 110 - 100
            assert status.gold == 40 # 15 + 25
            
            # HP was maxed out on level up
            assert status.hp == status.max_hp
            assert status.max_hp == 15 # 10 + 5
            
            inventory = json.loads(status.inventory)
            item_names = {i["name"] for i in inventory}
            assert "Pergaminho de Escudo" in item_names
            assert "Cajado do Fogo" not in item_names


def test_use_item_endpoint(client):
    # Test using a healing potion
    response = client.post(
        "/api/sessions/5/items/use",
        json={"character_id": 201, "item_id": "pot1"}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["healing_applied"] == 6
    assert data["used_item"]["name"] == "Poção de Cura Menor"
    
    # Check that character HP increased and item was removed
    with Session(engine) as session:
        status = session.exec(
            select(CharacterStatus)
            .where(CharacterStatus.game_session_id == 5)
            .where(CharacterStatus.character_id == 201)
        ).first()
        
        assert status.hp == 10 # 4 + 6 = 10
        inventory = json.loads(status.inventory)
        assert len(inventory) == 1
        assert inventory[0]["id"] == "swd1" # only staff remains
        
        # Check that System message turn was recorded
        messages = session.exec(
            select(CampaignMessage)
            .where(CampaignMessage.game_session_id == 5)
            .order_by(CampaignMessage.created_at)
        ).all()
        assert len(messages) == 1
        assert "usou Poção de Cura Menor" in messages[0].content
