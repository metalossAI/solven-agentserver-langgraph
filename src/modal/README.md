# Docling converter on Modal (GPU)

Document-to-markdown conversion runs **inside this Modal app** with GPU acceleration (Docling + VLM / Granite-Docling for PDF).

- **Deploy (required for production):** `modal deploy src/modal_docling/app.py`
- **Invocation:** The sandbox backend calls `modal.Function.from_name("solven-docling-converter", "convert_to_markdown").remote(content, filename)` when `MODAL_TOKEN_ID` or `USE_MODAL_DOCLING` is set.
- **Fallback:** If Modal is not deployed or the call fails, the backend uses local Docling (CPU, no VLM).
- **Optional:** Preloading the Granite model in the image (e.g. via `Image.run_function`) can be added for faster cold starts.
