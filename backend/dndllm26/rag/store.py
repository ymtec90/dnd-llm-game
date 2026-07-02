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
            table = db.open_table(self.table_name)
            try:
                if "lore_pack" not in table.schema.names:
                    db.drop_table(self.table_name)
                    return None
            except Exception:
                pass
            return table
        return None

    async def index_pdf(self, path: Path, document_id: int) -> int:
        return await self.index_document(path, document_id)

    async def index_document(self, path: Path, document_id: int, lore_pack: str | None = None) -> int:
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text = extract_pdf_text(path)
        elif suffix == ".txt":
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = path.read_text(encoding="latin-1")
        else:
            raise ValueError(f"Formato de arquivo não suportado: {suffix}")

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
                    "lore_pack": lore_pack or "",
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
        lore_pack: str | None = None,
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
        
        query_builder = table.search(vector)
        if lore_pack:
            query_builder = query_builder.where(f"lore_pack = '{lore_pack}'")
        elif document_ids:
            ids_str = ", ".join(str(i) for i in document_ids)
            query_builder = query_builder.where(f"document_id IN ({ids_str})")
            
        result = query_builder.limit(limit).to_list()
            
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
