"""
Загрузчик регламентных документов (PDF / DOCX) в векторное хранилище pgvector.

Запуск:
    python scripts/load_docs.py data/regulations/
"""

import re
from pathlib import Path
from typing import Generator

from sentence_transformers import SentenceTransformer
from sqlalchemy.orm import Session

from src.db.session import SessionLocal
from src.db.models import RegulationChunk

CHUNK_SIZE = 500       # символов
CHUNK_OVERLAP = 100    # символов перекрытия
MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

_model: SentenceTransformer | None = None


def _get_model() -> SentenceTransformer:
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def _extract_text_pdf(path: Path) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_text_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)


def _extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_text_pdf(path)
    elif suffix in (".docx", ".doc"):
        return _extract_text_docx(path)
    elif suffix == ".txt":
        return path.read_text(encoding="utf-8")
    else:
        raise ValueError(f"Неподдерживаемый формат: {path.suffix}")


def _chunk_text(text: str) -> Generator[str, None, None]:
    """Нарезает текст на фрагменты с перекрытием."""
    text = re.sub(r"\s+", " ", text).strip()
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        yield text[start:end]
        start += CHUNK_SIZE - CHUNK_OVERLAP


def load_document(filepath: str | Path) -> int:
    """Векторизует документ и сохраняет фрагменты в БД. Возвращает кол-во чанков."""
    filepath = Path(filepath)
    model = _get_model()
    text = _extract_text(filepath)
    chunks = list(_chunk_text(text))
    if not chunks:
        return 0

    embeddings = model.encode(chunks, show_progress_bar=False).tolist()

    rows = [
        RegulationChunk(
            doc_name=filepath.name,
            chunk_text=chunk,
            embedding=emb,
        )
        for chunk, emb in zip(chunks, embeddings)
    ]

    session: Session = SessionLocal()
    try:
        # Удаляем старые чанки того же документа
        session.query(RegulationChunk).filter(
            RegulationChunk.doc_name == filepath.name
        ).delete()
        session.bulk_save_objects(rows)
        session.commit()
        return len(rows)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def load_directory(dirpath: str | Path) -> dict[str, int]:
    """Загружает все документы из папки. Возвращает {имя_файла: кол-во_чанков}."""
    dirpath = Path(dirpath)
    results = {}
    for path in sorted(dirpath.iterdir()):
        if path.suffix.lower() in (".pdf", ".docx", ".doc", ".txt"):
            try:
                count = load_document(path)
                results[path.name] = count
                print(f"  ✓ {path.name}: {count} чанков")
            except Exception as e:
                print(f"  ✗ {path.name}: {e}")
    return results
