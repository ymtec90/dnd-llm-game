from pathlib import Path
from dndllm26.rag.store import rag_store

LORE_PACKS = {
    "grimdark_scifi": {
        "id": "grimdark_scifi",
        "name": "Ficção Científica Sombria (Aegis-9)",
        "description": "O ano é 4192. A humanidade vaga na degradada Aegis-9 sob a ameaça de IAs rebeldes e ciborgues corrompidos.",
        "tone_prompt": (
            "Você é o Mestre (DM) narrando em um cenário de FICÇÃO CIENTÍFICA SOMBRIA (Grimdark Sci-Fi) a bordo da Aegis-9. "
            "Use um tom frio, claustrofóbico, tecnológico mas degradado. Enfatize a escassez de oxigênio, a radiação constante "
            "e cibernética corrompida. Não use tom clássico de fantasia medieval."
        )
    },
    "historico_brasil": {
        "id": "historico_brasil",
        "name": "Brasil Colonial (Minas 1789)",
        "description": "O ano é 1789, em Vila Rica (Ouro Preto). Conspirações da Inconfidência Mineira contra a Coroa Portuguesa.",
        "tone_prompt": (
            "Você é o Mestre (DM) narrando em um cenário HISTÓRICO REALISTA DO BRASIL COLONIAL em 1789. "
            "Use vocabulário formal de época colonial, abordando tavernas de pedra, dragões portugueses da rainha, "
            "derrama e conspiradores inconfidentes. Não use tom de fantasia ou ficção científica."
        )
    }
}


def get_lore_packs() -> list[dict]:
    return [
        {
            "id": pack["id"],
            "name": pack["name"],
            "description": pack["description"]
        }
        for pack in LORE_PACKS.values()
    ]


async def ensure_lore_pack_indexed(lore_pack_id: str) -> None:
    if lore_pack_id not in LORE_PACKS:
        return
        
    table = rag_store._table()
    if table is not None:
        # Check if table already has indexed rows for this pack
        try:
            results = await rag_store.search("Aegis ou Vila Rica", limit=10, lore_pack=lore_pack_id)
            if results:
                return  # already indexed!
        except Exception:
            pass
            
    # Index files
    dir_path = Path("data/lore") / lore_pack_id
    if not dir_path.exists():
        return
        
    for p in dir_path.glob("*.txt"):
        await rag_store.index_document(p, document_id=-99, lore_pack=lore_pack_id)
