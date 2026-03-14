"""
Modal app: Docling document-to-markdown conversion on GPU.
Multi-format pipelines (PDF with VLM, DOCX, PPTX, images, HTML, etc.) run inside the Modal GPU function.
See: https://docling-project.github.io/docling/examples/run_with_formats/
Deploy with: modal deploy src/modal_docling/app.py
Invoke from agent: modal.Function.from_name("solven-docling-converter", "convert_to_markdown").remote(content, filename)
"""

import os
import tempfile
import modal

# Image: Docling + VLM deps. Optional run_function can preload Granite model for faster cold start.
docling_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "docling[vlm]",
        "torch",
        "transformers",
    )
)

app = modal.App("solven-docling-converter", image=docling_image)


def _build_converter():
    """Build DocumentConverter with multi-format pipelines: VLM for PDF, SimplePipeline for DOCX/PPTX/HTML, defaults for images/Excel/MD/CSV."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import VlmConvertOptions, VlmPipelineOptions
    from docling.document_converter import (
        DocumentConverter,
        ExcelFormatOption,
        HTMLFormatOption,
        ImageFormatOption,
        PdfFormatOption,
        WordFormatOption,
        PowerpointFormatOption,
        MarkdownFormatOption,
        CsvFormatOption,
    )
    from docling.pipeline.simple_pipeline import SimplePipeline
    from docling.pipeline.vlm_pipeline import VlmPipeline

    vlm_options = VlmConvertOptions.from_preset("granite_docling")
    return DocumentConverter(
        allowed_formats=[
            InputFormat.PDF,
            InputFormat.IMAGE,
            InputFormat.DOCX,
            InputFormat.PPTX,
            InputFormat.HTML,
            InputFormat.XLSX,
            InputFormat.MD,
            InputFormat.CSV,
        ],
        format_options={
            InputFormat.PDF: PdfFormatOption(
                pipeline_cls=VlmPipeline,
                pipeline_options=VlmPipelineOptions(vlm_options=vlm_options),
            ),
            InputFormat.DOCX: WordFormatOption(pipeline_cls=SimplePipeline),
            InputFormat.PPTX: PowerpointFormatOption(pipeline_cls=SimplePipeline),
            InputFormat.HTML: HTMLFormatOption(pipeline_cls=SimplePipeline),
            InputFormat.IMAGE: ImageFormatOption(),
            InputFormat.XLSX: ExcelFormatOption(pipeline_cls=SimplePipeline),
            InputFormat.MD: MarkdownFormatOption(pipeline_cls=SimplePipeline),
            InputFormat.CSV: CsvFormatOption(pipeline_cls=SimplePipeline),
        },
    )


@app.function(gpu="L40S", timeout=300)
def convert_to_markdown(content: bytes, filename: str) -> str:
    """
    Convert document bytes to markdown. Runs entirely on the Modal GPU.
    Multi-format: PDF (VLM/Granite), DOCX, PPTX, images, HTML, XLSX, MD, CSV.
    """
    safe_name = os.path.basename(filename) if filename else "document"
    if not safe_name or safe_name.startswith("."):
        safe_name = "document.pdf"
    elif "." not in safe_name:
        safe_name = safe_name + ".bin"
    ext = os.path.splitext(safe_name)[1] or ".bin"
    fd, tmp_path = tempfile.mkstemp(suffix=ext, prefix="docling_")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
    except Exception:
        os.close(fd)
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    try:
        converter = _build_converter()
        result = converter.convert(tmp_path)
        return result.document.export_to_markdown()
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
