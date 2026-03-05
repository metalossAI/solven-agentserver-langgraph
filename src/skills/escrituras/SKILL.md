---
name: Escrituras
description: Work with notary legal document templates: set up user-uploaded templates by normalizing fill points to {{placeholder_name}}, then build fast fill scripts. Use when working with notary documents, escrituras, or Office documents (.doc, .docx) and PDF templates.
---

# Escrituras (Notary Fast Fill)

## Purpose

This skill enables the agent to **set up** user-uploaded notary templates (so they use a single placeholder format) and to **create fast fill scripts** that fill them with workspace data. User templates can have different formats and errors; the initial **template setup** step normalizes them so the fast fill script is simple and consistent.

## Principle: normalize first, then fill

Do **not** analyse the template to adapt the script to existing placeholders. Instead:

1. **Template setup (first):** Analyse the whole template, identify where fill-in data is needed (NIFs, names, dates, amounts, addresses, etc.), and **edit the template** to add a single format there: `{{placeholder_name}}`. Do not change the template’s format, layout, or styling — only replace or insert `{{placeholder_name}}` at those positions.
2. **Fast fill script (second):** Build a script that replaces each `{{placeholder_name}}` with data from a JSON/dict. The script always assumes this format.

## Reference: Escrituras structure

### Naming convention

The anchor is the standard model name in `assets/`. All scripts and references for that model use the same prefix.

- Model in assets: `42_modelo.pdf` → script: `scripts/fill/42_modelo_fill.py`, reference: `references/42_modelo.md`

### Directory layout

```
skill-root/
├── SKILL.md
├── assets/          # Template files (.doc, .docx, PDF) uploaded by user
├── references/      # Optional: per-model instructions and field list
└── scripts/         # Base analysis and reference scripts (synced)
    ├── analyze_template.py       # base analysis
    ├── analyze_docx_placeholders.py
    ├── example_fill.py            # reference implementation
    └── fill/                      # model-specific fill scripts go here
        └── {model_anchor}_fill.py
```

---

## Step 1: Template Normalization in-place (for every new user-uploaded template)

After finding the template in /assets for the correponding document, analyse it and edit the pdf/office doc to standarize placeholders to match {{placeholder_name}} then save the template in place.


### 1.1 Read and analyse the template

- Locate the template in `assets/` (and any notes in `references/`).
- **Office documents (.doc, .docx):** Use the docx skill to read and edit. For legacy **.doc** files, convert to .docx first (e.g. using the docx skill’s conversion: LibreOffice/soffice `--convert-to docx`), then read and edit the .docx. **PDF:** Use the pdf skill to inspect content; if editing is required, consider converting to DOCX for setup or document limits in `references/`.
- Read the template in full and identify **fill points**: places that should be filled with case-specific data, e.g.:
  - NIFs / CIFs
  - Names (comparecientes, notarios, etc.)
  - Dates
  - Amounts, prices, quantities
  - Addresses
  - Any blank, underscore line, or inconsistent placeholder that clearly represents data to fill

### 1.2 Edit the template in place: add only `{{placeholder_name}}`

- **Edit the template in place** — modify the existing file in `assets/` directly. Do **not** create a new template file or a copy with the clean placeholders; the original document must be updated.
- **Use the docx skill** to edit the template. Change **only** the fill points identified above.
- At each fill point, replace the existing text (or blank) with a single placeholder in this form: `{{placeholder_name}}`. Use clear, consistent names (e.g. `{{NIF_COMPARECIENTE}}`, `{{NOMBRE}}`, `{{FECHA}}`, `{{IMPORTE}}`).
- **Do not change** the template’s format: no layout, fonts, margins, tables, or structure. Only the content at fill points becomes `{{placeholder_name}}`.
- Save the template in place (overwrite the same file in `assets/`). Do not create versioned copies or a separate “clean” template unless the user asks.

### 1.3 Document the field list (optional)

- Run `scripts/analyze_docx_placeholders.py` on the updated template to list all `{{key}}` keys.
- Optionally write the field list and any quirks to `references/{model}.md` so the fast fill script and users know the expected keys.

---

## Step 2: Create the fast fill script

After the template is set up, it contains only `{{placeholder_name}}` placeholders. Create a script that fills them.

### 2.1 Get the field list

- Run `scripts/analyze_docx_placeholders.py path/to/template.docx` (or use the list in `references/{model}.md`) to get the exact placeholder names.

### 2.2 Implement the fill script

- Create the script in **scripts/fill/**: `scripts/fill/{model_anchor}_fill.py` (e.g. `scripts/fill/42_modelo_fill.py`). Model-specific fill scripts belong in `scripts/fill/`, not in `scripts/` directly.
- The script must: read the template path and a data source (JSON or dict), replace every `{{key}}` with the corresponding value, and write the filled document. Use `scripts/example_fill.py` as reference; place the new script under `scripts/fill/`.
- Input: template path + data (JSON/dict). Output: filled document path.
- For large documents, work section-by-section or page-by-page to avoid mistakes.
- Add a short docstring and usage example in the script.

### 2.3 Document

- Add an entry for the new script in `scripts/README.md` (command and purpose) if the skill uses it. Optionally update `references/{model}.md` with the field list.

---

## Rules (template setup and fill)

- **Edit the template in place.** Do not create a new template file with the clean placeholders; modify the original document in `assets/` directly.
- **Do not change template format or structure** unless the user explicitly asks. Only add or replace text at fill points with `{{placeholder_name}}`.
- Avoid creating versioned copies (e.g. `model_v2.docx`) or a separate output template unless requested.
- Use comments and redlining when editing drafts; keep final templates clean with only `{{key}}` placeholders.
- One fill script per template; keep it executable and documented.

---

## Utility scripts (this skill)

Analysis and reference scripts live in `scripts/`. **Model-specific fill scripts** go in **scripts/fill/** (e.g. `scripts/fill/42_modelo_fill.py`).

- **scripts/analyze_template.py** — Detects format (docx/pdf) and lists existing placeholders if any. Useful after setup to confirm `{{key}}` list. Output: JSON.
  ```bash
  python scripts/analyze_template.py path/to/template.docx
  ```
- **scripts/analyze_docx_placeholders.py** — List `{{KEY}}` placeholders in a DOCX (or in a .doc after converting to .docx). Use after template setup to get the exact keys for the fill script.
  ```bash
  python scripts/analyze_docx_placeholders.py path/to/template.docx
  python scripts/analyze_docx_placeholders.py path/to/template.docx --json
  ```
- **scripts/example_fill.py** — Reference: fill a DOCX (or .doc converted to .docx) by replacing `{{KEY}}` with values from JSON. Use as base for model-specific fill scripts, which you place in **scripts/fill/**.
  ```bash
  python scripts/example_fill.py template.docx data.json -o filled.docx
  ```
- See **scripts/README.md** for the full script list.

---

## Data format for fill scripts

After setup, all placeholders use the form `{{placeholder_name}}`. The data file (e.g. JSON) must provide values for each key:

```json
{
  "NOMBRE": "Juan Pérez",
  "NIF": "12345678A",
  "FECHA": "2025-03-05"
}
```

---

## Activation examples

- "Set up the compraventa template in assets and then create a fast fill script."
- "The user uploaded a new .doc / DOCX template; run template setup and add a fill script."
- "Normalize the escritura template to use {{placeholders}} and build the fill script."
- "First time using this template — set it up (add {{placeholder_name}} only where needed) and create the fast fill script."
