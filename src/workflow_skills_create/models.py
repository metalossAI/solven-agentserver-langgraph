from typing import List, Optional
from pydantic import BaseModel, Field


class SkillMD(BaseModel):
    """
    Representación estructurada de un documento SKILL.md.
    Cada campo mapea directamente a una sección del archivo markdown.
    """

    # -------------------------
    # FRONTMATTER YAML
    # -------------------------

    name: str = Field(
        ...,
        description=(
            "Identificador único de la habilidad. Debe estar en minúsculas, separado por guiones, "
            "máximo 64 caracteres. Ejemplo: 'legal-document-drafting'."
        ),
        min_length=1,
        max_length=64,
    )

    description: str = Field(
        ...,
        description=(
            "Resumen breve y objetivo que describa qué hace y cuándo debe usarse."
            "Evita lenguaje subjetivo. Máximo 1024 caracteres."
        ),
        max_length=1024,
    )

    # -------------------------
    # CONTENIDO LEGIBLE PARA HUMANOS
    # -------------------------

    title: str = Field(
        ...,
        description=(
            "Título legible para humanos de la habilidad. Aparece como el encabezado H1 "
            "después del frontmatter YAML."
        ),
    )

    overview: str = Field(
        ...,
        description=(
            "Explicación de alto nivel del propósito de la habilidad y sus principales casos de uso. "
            "Debe ser concisa e informativa."
        ),
    )

    when_to_use: List[str] = Field(
        ...,
        description=(
            "Lista de disparadores o situaciones claras en las que esta habilidad debe aplicarse. "
            "Cada elemento debe describir una intención del usuario o un escenario."
        ),
        min_items=1,
    )

    # -------------------------
    # GUÍA DE EJECUCIÓN
    # -------------------------

    workflow: List[str] = Field(
        ...,
        description=(
            "Instrucciones paso a paso que el agente debe seguir al ejecutar esta habilidad. "
            "Los pasos deben ser secuenciales, explícitos e imperativos."
        ),
        min_items=1,
    )

    examples: Optional[List[str]] = Field(
        default=None,
        description=(
            "Ejemplos concretos de uso que demuestren cómo debe aplicarse la habilidad. "
            "Cada ejemplo puede incluir una solicitud del usuario y el comportamiento esperado."
        ),
    )

    constraints: Optional[List[str]] = Field(
        default=None,
        description=(
            "Guardarraíles, limitaciones o reglas explícitas que deben aplicarse "
            "al usar esta habilidad."
        ),
    )

    assets_and_references: Optional[List[str]] = Field(
        default=None,
        description=(
            "Lista de archivos externos (plantillas, referencias, ejemplos) usados por esta habilidad, "
            "con breves explicaciones de cómo deben utilizarse."
        ),
    )

    progressive_disclosure_notes: Optional[str] = Field(
        default=None,
        description=(
            "Guía opcional que apunte a archivos de referencia adicionales para contenido extendido, "
            "utilizada para mantener el SKILL.md conciso."
        ),
    )

    # -------------------------
    # RENDERIZADO
    # -------------------------

    def to_markdown(self) -> str:
        """
        Renderiza el objeto SkillMD a un documento SKILL.md completamente formateado.
        """

        md = []

        # Frontmatter YAML
        md.append("---")
        md.append(f"name: {self.name}")
        md.append(f"description: {self.description}")
        md.append("---\n")

        # Título
        md.append(f"# {self.title}\n")

        # Resumen
        md.append("## Overview")
        md.append(self.overview + "\n")

        # Cuándo usar
        md.append("## When to Use This Skill")
        for item in self.when_to_use:
            md.append(f"- {item}")
        md.append("")

        # Flujo de trabajo
        md.append("## Instructions / Workflow")
        for i, step in enumerate(self.workflow, start=1):
            md.append(f"{i}. {step}")
        md.append("")

        # Ejemplos
        if self.examples:
            md.append("## Examples")
            for example in self.examples:
                md.append(example)
                md.append("")

        # Restricciones
        if self.constraints:
            md.append("## Constraints & Guardrails")
            for rule in self.constraints:
                md.append(f"- {rule}")
            md.append("")

        # Activos y referencias
        if self.assets_and_references:
            md.append("## Assets & References")
            for asset in self.assets_and_references:
                md.append(f"- {asset}")
            md.append("")

        # Divulgación progresiva
        if self.progressive_disclosure_notes:
            md.append("## Progressive Disclosure")
            md.append(self.progressive_disclosure_notes)
            md.append("")

        return "\n".join(md)