import io
from pathlib import Path

from pypdf import PdfReader

SUPPORTED = {".md", ".markdown", ".txt", ".pdf"}


def load_text(filename: str, data: bytes) -> str:
    """Turn an uploaded file into markdown-ish text the chunker understands."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError(f"Unsupported file type {suffix!r}. Supported: {sorted(SUPPORTED)}")
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
        text = "\n\n".join(p for p in pages if p)
        if not text:
            raise ValueError("No extractable text in this PDF (scanned PDFs need OCR first).")
        return text
    return data.decode("utf-8", errors="replace")


def title_from_filename(filename: str) -> str:
    stem = Path(filename).stem
    stem = stem.split("-", 1)[1] if stem[:2].isdigit() and "-" in stem else stem
    return stem.replace("-", " ").replace("_", " ").strip().title()
