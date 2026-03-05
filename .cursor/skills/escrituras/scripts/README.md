# Escrituras scripts

| Script | Purpose |
|--------|---------|
| `analyze_template.py` | Detects format (docx/pdf) and lists existing placeholders. Use after template setup to confirm the `{{key}}` list. Output: JSON. |
| `analyze_docx_placeholders.py` | List `{{KEY}}` placeholder keys in a DOCX. Use **after template setup** to get the exact keys for building the fast fill script. |
| `example_fill.py` | Reference implementation: fill a DOCX by replacing `{{KEY}}` with values from JSON. Use as base for `{model_anchor}_fill.py`. |

**Requirements:** `example_fill.py` needs `pip install python-docx`. `analyze_template.py` for PDF form detection needs `pip install pypdf`.

**Workflow (see SKILL.md):** (1) **Template setup** — Use the docx skill to read the template, find fill points (NIFs, names, dates, etc.), and edit the template to add only `{{placeholder_name}}` at those positions without changing format. (2) Run `analyze_docx_placeholders.py` to list keys. (3) Create `{model_anchor}_fill.py` from `example_fill.py` so the script matches the template’s `{{key}}` fields.
