"""Convert document bytes to markdown via Modal (Docling GPU) or local Docling.

Shared by S3 backend and E2B sandbox backend so behavior and env flags stay aligned.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def convert_bytes_to_markdown(content: bytes, filename: str) -> str:
    """Convert PDF/DOCX/XLSX/etc. bytes to markdown.

    Prefer Modal GPU (Docling VLM) when ``MODAL_TOKEN_ID`` or ``USE_MODAL_DOCLING`` is set;
    fall back to local Docling on failure.
    """
    use_modal = bool((os.getenv("MODAL_TOKEN_ID") or os.getenv("USE_MODAL_DOCLING") or "").strip())
    if use_modal:
        try:
            import modal

            fn = modal.Function.from_name("solven-docling-converter", "convert_to_markdown")
            return fn.remote(content, filename)
        except Exception:
            pass

    ext = (Path(filename).suffix or "").lower()
    suffix = ext or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(content)
        tmp.flush()
        tmp_path = tmp.name
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
