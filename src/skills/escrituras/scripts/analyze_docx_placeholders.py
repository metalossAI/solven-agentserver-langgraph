#!/usr/bin/env python3
"""
Analyse a DOCX template and list placeholder keys (e.g. {{KEY}}) to build a field map.
Use this when creating a fast fill script for a notary template.

Usage:
  python scripts/analyze_docx_placeholders.py path/to/template.docx
  python scripts/analyze_docx_placeholders.py path/to/template.docx --json   # JSON output
"""

import re
import sys
import json
import zipfile
from pathlib import Path

# Same shapes as analyze_template.py: {{KEY}}, {KEY}, *KEY*, [KEY], (KEY), standalone *
PLACEHOLDER_PATTERNS = [
    re.compile(r"\{\{([^}]+)\}\}"),
    re.compile(r"\{([^}]+)\}"),
    re.compile(r"\*([^*]+)\*"),
    re.compile(r"\[([^\]]+)\]"),
    re.compile(r"\(([^)]+)\)"),
]
STANDALONE_ASTERISK = re.compile(r"(?<!\S)\*(?!\S)")


def extract_placeholders(docx_path: str) -> list[str]:
    """Extract placeholder keys from document.xml ({{KEY}}, {KEY}, *KEY*, [KEY], (KEY), *)."""
    path = Path(docx_path)
    if not path.suffix.lower() == ".docx":
        raise ValueError("Expected a .docx file")
    if not path.exists():
        raise FileNotFoundError(path)

    keys = []
    with zipfile.ZipFile(path, "r") as z:
        if "word/document.xml" not in z.namelist():
            return keys
        with z.open("word/document.xml") as f:
            text = f.read().decode("utf-8")
    for pattern in PLACEHOLDER_PATTERNS:
        keys.extend(pattern.findall(text))
    if STANDALONE_ASTERISK.search(text):
        keys.append("*")
    keys = list(dict.fromkeys(k.strip() for k in keys))
    return sorted(keys)


def main():
    if len(sys.argv) < 2:
        print("Usage: analyze_docx_placeholders.py <template.docx> [--json]", file=sys.stderr)
        sys.exit(1)
    docx_path = sys.argv[1]
    as_json = "--json" in sys.argv

    try:
        keys = extract_placeholders(docx_path)
    except (ValueError, FileNotFoundError) as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)

    if as_json:
        print(json.dumps({"placeholders": keys}, indent=2))
    else:
        for k in keys:
            print(k)


if __name__ == "__main__":
    main()
