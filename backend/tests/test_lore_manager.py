import pytest
from sqlmodel import Session, SQLModel
from fastapi.testclient import TestClient

from dndllm26.main import app
from dndllm26.db.session import engine
from dndllm26.db.models import Campaign, WorldState, GameSession
from dndllm26.rag.store import rag_store
from dndllm26.rag.lore_manager import get_lore_packs, ensure_lore_pack_indexed

@pytest.fixture(name="client")
def client_fixture():
    SQLModel.metadata.create_all(engine)
    
    with Session(engine) as session:
        # Seed Campaign
        c = Campaign(id=30, title="Universo Campanha", setting="Original", tone="neutro")
        session.add(c)
        ws = WorldState(id=30, campaign_id=30, current_location="Início", active_objective="Objetivo", choices_json="[]")
        session.add(ws)
        session.commit()
        
    with TestClient(app) as client:
        yield client
    SQLModel.metadata.drop_all(engine)


@pytest.mark.anyio
async def test_lore_packs_list(client):
    response = client.get("/api/lore/packs")
    assert response.status_code == 200
    packs = response.json()
    assert len(packs) == 2
    ids = {p["id"] for p in packs}
    assert "grimdark_scifi" in ids
    assert "historico_brasil" in ids


@pytest.mark.anyio
async def test_rag_segmentation_isolation(client):
    # Force indexing of lore packs
    await ensure_lore_pack_indexed("grimdark_scifi")
    await ensure_lore_pack_indexed("historico_brasil")
    
    # 1. Search for Aegis under grimdark_scifi -> should return Aegis info
    sci_fi_results = await rag_store.search("Aegis-9 Nave Estelar", limit=4, lore_pack="grimdark_scifi")
    assert len(sci_fi_results) >= 1
    for r in sci_fi_results:
        assert "Aegis-9" in r["text"] or "Grimdark" in r["text"]
    
    # 2. Search for Ouro Preto under historico_brasil -> should return Vila Rica info
    brasil_results = await rag_store.search("Vila Rica Inconfidência Ouro Preto", limit=4, lore_pack="historico_brasil")
    assert len(brasil_results) >= 1
    for r in brasil_results:
        assert "Vila Rica" in r["text"] or "Brasil" in r["text"]


@pytest.mark.anyio
async def test_create_session_with_lore_pack(client):
    response = client.post(
        "/api/sessions",
        json={"campaign_id": 30, "name": "Campanha Futurista", "lore_pack": "grimdark_scifi"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["lore_pack"] == "grimdark_scifi"
    assert data["name"] == "Campanha Futurista"
    
    # Check that game session in db contains lore pack
    with Session(engine) as session:
        db_sess = session.get(GameSession, data["id"])
        assert db_sess is not None
        assert db_sess.lore_pack == "grimdark_scifi"
