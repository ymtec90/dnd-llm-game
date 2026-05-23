from pathlib import Path

from pypdf import PdfReader

from dndllm26.core.settings import get_settings
from dndllm26.llm.ollama_client import ollama_service


def chunk_text(text: str, size: int = 900, overlap: int = 120) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + size, len(words))
        chunks.append(" ".join(words[start:end]))
        if end == len(words):
            break
        start = max(0, end - overlap)
    return [chunk for chunk in chunks if chunk.strip()]


def extract_pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class RagStore:
    def __init__(self) -> None:
        settings = get_settings()
        self.db_path = settings.lancedb_dir
        self.db = None
        self.table_name = "lore_chunks"

    def _db(self):
        if self.db is None:
            import lancedb

            self.db = lancedb.connect(self.db_path)
        return self.db

    def _table(self):
        db = self._db()
        if self.table_name in db.table_names():
            return db.open_table(self.table_name)
        return None

    async def index_pdf(self, path: Path, document_id: int) -> int:
        text = extract_pdf_text(path)
        chunks = chunk_text(text)
        rows = []
        for idx, chunk in enumerate(chunks):
            vector = await ollama_service.embed(chunk)
            if not vector:
                continue
            rows.append(
                {
                    "vector": vector,
                    "document_id": document_id,
                    "filename": path.name,
                    "chunk_index": idx,
                    "text": chunk,
                }
            )
        if not rows:
            return 0
        table = self._table()
        if table is None:
            self._db().create_table(self.table_name, rows)
        else:
            table.add(rows)
        return len(rows)

    async def search(
        self,
        query: str,
        limit: int = 4,
        document_ids: list[int] | None = None,
    ) -> list[dict]:
        table = self._table()
        if table is None:
            return []
        if document_ids is not None and not document_ids:
            return []
        try:
            vector = await ollama_service.embed(query)
        except Exception:
            return []
        if not vector:
            return []
        search_limit = max(limit, limit * 4) if document_ids else limit
        result = table.search(vector).limit(search_limit).to_list()
        if document_ids:
            allowed = set(document_ids)
            result = [row for row in result if row.get("document_id") in allowed][:limit]
        return [
            {
                "document_id": row.get("document_id"),
                "filename": row.get("filename"),
                "chunk_index": row.get("chunk_index"),
                "text": row.get("text"),
                "score": row.get("_distance"),
            }
            for row in result
        ]


rag_store = RagStore()
