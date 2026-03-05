#!/usr/bin/env python3
"""
Analyse a notary template (DOCX or PDF) and list existing placeholders or form fields.
After template setup (see SKILL.md), templates use only {{placeholder_name}}; this script
reports format and any detected placeholders so you can confirm the field list for the fill script.

Usage:
  python scripts/analyze_template.py path/to/template.docx
  python scripts/analyze_template.py path/to/template.pdf
"""

import re
import sys
import json
import zipfile
from pathlib import Path

# Placeholder patterns: many shapes — {{KEY}}, {KEY}, *KEY*, [KEY], (KEY), [*], standalone *
PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{([^}]+)\}\}"),   # {{KEY}}
    re.compile(r"\{([^}]+)\}"),         # {KEY} or {*}
    re.compile(r"\*([^*]+)\*"),         # *KEY*
    re.compile(r"\[([^\]]+)\]"),        # [KEY] or [*]
    re.compile(r"\(([^)]+)\)"),        # (KEY) or (*)
]
STANDALONE_ASTERISK = re.compile(r"(?<!\S)\*(?!\S)")  # * as placeholder (space/punct-separated)


def analyze_docx(path: Path) -> dict:
    """Detect fill strategy and fields in a DOCX."""
    result = {"format": "docx", "strategy": "none", "fields": []}
    try:
        with zipfile.ZipFile(path, "r") as z:
            if "word/document.xml" not in z.namelist():
                return result
            with z.open("word/document.xml") as f:
                text = f.read().decode("utf-8")
    except (zipfile.BadZipFile, KeyError) as e:
        result["error"] = str(e)
        return result

    # Collect all placeholder keys from any supported pattern
    all_keys = []
    for pattern in PLACEHOLDER_PATTERNS:
        all_keys.extend(pattern.findall(text))
    if STANDALONE_ASTERISK.search(text):
        all_keys.append("*")
    all_keys = list(dict.fromkeys(k.strip() for k in all_keys))

    if all_keys:
        result["strategy"] = "placeholders"
        result["fields"] = sorted(all_keys)
        return result

    # Optional: detect DOCX content controls (w:sdt) — simple check
    if "<w:sdt " in text or "w:sdt" in text:
        result["strategy"] = "form_fields"
        result["note"] = "Content controls detected; list field names manually or from XML."
        return result

    # No placeholders and no obvious form fields → find_replace or manual
    result["strategy"] = "find_replace"
    result["note"] = "No placeholders found. Define search strings in references/{model}.md and map data keys to them."
    return result


def analyze_pdf(path: Path) -> dict:
    """Detect fill strategy and fields in a PDF (AcroForm fields if present)."""
    result = {"format": "pdf", "strategy": "none", "fields": []}
    try:
        import pypdf
    except ImportError:
        result["note"] = "pypdf not installed; run: pip install pypdf. Cannot list form fields."
        return result
    try:
        reader = pypdf.PdfReader(str(path))
        fields = reader.get_fields()
        if fields:
            result["strategy"] = "form_fields"
            result["fields"] = sorted(fields.keys())
        else:
            result["strategy"] = "find_replace"
            result["note"] = "No AcroForm fields. Use text coordinates or define find/replace map in references/."
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: analyze_template.py <template.docx|template.pdf>", file=sys.stderr)
        sys.exit(1)
    template_path = Path(sys.argv[1])
    if not template_path.exists():
        print(f"File not found: {template_path}", file=sys.stderr)
        sys.exit(1)

    suffix = template_path.suffix.lower()
    if suffix == ".docx":
        out = analyze_docx(template_path)
    elif suffix == ".pdf":
        out = analyze_pdf(template_path)
    else:
        print(f"Unsupported format: {suffix}. Use .docx or .pdf.", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
