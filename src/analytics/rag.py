"""
RAG-поиск по векторному хранилищу регламентных документов.
"""

from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from src.db.session import SessionLocal

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
TOP_K = 5

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def search_regulations(query: str, top_k: int = TOP_K) -> list[str]:
    """
    Ищет top_k наиболее релевантных фрагментов регламентов по запросу.
    Возвращает список текстовых фрагментов.
    """
    model = _get_model()
    embedding = model.encode(query).tolist()

    # pgvector оператор <-> = cosine distance
    sql = text("""
        SELECT chunk_text
        FROM regulation_chunks
        ORDER BY embedding <-> cast(:emb AS vector)
        LIMIT :k
    """)

    session = SessionLocal()
    try:
        rows = session.execute(sql, {"emb": str(embedding), "k": top_k}).fetchall()
        return [row[0] for row in rows]
    finally:
        session.close()
