from __future__ import annotations
import logging
import os
from typing import Callable, Awaitable
from langsmith import AsyncClient
from langsmith.utils import LangSmithError
from langchain.agents.middleware import AgentMiddleware, ModelRequest, dynamic_prompt
from langchain_core.messages import SystemMessage
from langchain_core.prompts import ChatPromptTemplate

from deepagents.middleware.skills import (
    SkillsMiddleware as BaseSkillsMiddleware,
    SkillMetadata,
    _format_skill_annotations,
)

# Fallback when LangSmith returns 403 or is unreachable (e.g. wrong key or private prompt).
# Uses the same variable names as solven-main so format(**variables) works.
_DEFAULT_SOLVEN_MAIN = (
    "Eres un asistente experto. Fecha: {date}. Usuario: {name} (rol: {role}). "
    "Idioma: {language}. Contexto del ticket: {ticket}"
)

async def build_prompt_template(prompt_id: str, variables: dict) -> str:
    """Pull a prompt by id and format it with the given variables. On LangSmith error (e.g. 403), use a local fallback."""
    try:
        client = AsyncClient()
        base_prompt: ChatPromptTemplate = await client.pull_prompt(prompt_id)
        return base_prompt.format(**variables)
    except LangSmithError as e:
        logging.warning("LangSmith pull_prompt failed (%s), using fallback for prompt_id=%s", e, prompt_id)
    except Exception as e:
        logging.warning("pull_prompt failed (%s), using fallback for prompt_id=%s", e, prompt_id)
    # Fallback: format a minimal template with whatever variables we have (no KeyError)
    try:
        return _DEFAULT_SOLVEN_MAIN.format(**{k: variables.get(k, "") for k in ("date", "name", "role", "language", "ticket")})
    except Exception:
        return _DEFAULT_SOLVEN_MAIN.format(date="", name="", role="", language="español", ticket="")


def create_prompt_middleware(
    prompt_id: str,
    get_variables: Callable[[ModelRequest], Awaitable[dict]],
) -> Callable[[ModelRequest], Awaitable[SystemMessage]]:
    """
    Returns a @dynamic_prompt middleware that builds the system message for the given prompt_id.
    The returned function can be passed at runtime (e.g. to create_agent(middleware=[...])).
    get_variables(request) is called to obtain the format variables; the formatted template
    is prepended to the request's system_message content_blocks.
    """
    @dynamic_prompt
    async def middleware(request: ModelRequest) -> SystemMessage:
        variables = await get_variables(request)
        initial_prompt = await build_prompt_template(prompt_id, variables)
        system_prompt = request.system_message
        new_content = [
            {"type": "text", "text": f"{initial_prompt}\n\n"},
            *system_prompt.content_blocks,
        ]
        return SystemMessage(content=new_content)
    return middleware

def _skill_name_from_path(path: str) -> str | None:
    """Derive skill folder name from path, e.g. '/.solven/skills/docx/SKILL.md' -> 'docx'."""
    if not path:
        return None
    parts = path.rstrip("/").replace("\\", "/").split("/")
    # .../skills/<name>/... or .../skills/<name> or <name>/SKILL.md
    if "skills" in parts:
        i = parts.index("skills")
        if i + 1 < len(parts):
            return parts[i + 1]
    return parts[-2] if len(parts) >= 2 and parts[-1].upper().startswith("SKILL") else (parts[-1] if parts else None)


class SkillsMiddleware(BaseSkillsMiddleware):
    """
    Custom SkillsMiddleware for Solven.
    Same behavior as deepagents.middleware.skills.SkillsMiddleware, but the
    system prompt explicitly instructs the model to use the `load_skill` tool
    to read full skill instructions instead of reading the files directly.
    Supports exclude_skills to filter out skills by name or path (e.g. ["docx"]).
    """

    def __init__(
        self,
        *,
        backend,
        sources: list[str],
        exclude_skills: list[str] | None = None,
        **kwargs,
    ) -> None:
        # Base only accepts (backend, sources); do not pass exclude_skills or other kwargs
        raw = list(exclude_skills or []) + list(kwargs.pop("exclude_skills", None) or [])
        super().__init__(backend=backend, sources=sources)
        self._exclude_skills: set[str] = {s.strip().lower() for s in raw if s}

    def _filtered_skills(self, skills: list[SkillMetadata]) -> list[SkillMetadata]:
        """Return skills with exclude_skills removed (match by name or path-derived name)."""
        if not self._exclude_skills:
            return skills
        out: list[SkillMetadata] = []
        for s in skills:
            name = (s.get("name") or "").strip().lower()
            path_name = _skill_name_from_path(s.get("path") or "")
            path_name = (path_name or "").lower()
            excluded = name in self._exclude_skills or path_name in self._exclude_skills
            if not excluded:
                out.append(s)
        return out

    def _format_skills_list(self, skills: list[SkillMetadata]) -> str:
        """Format skills metadata for display in system prompt.

        Overridden to nudge the model towards using the `load_skill` tool.
        Excluded skills (exclude_skills) are filtered out before formatting.
        """
        skills = self._filtered_skills(skills)
        if not skills:
            paths = [f"{source_path}" for source_path in self.sources]
            return (
                f"(No skills available yet. You can create skills in "
                f"{' or '.join(paths)})"
            )

        lines: list[str] = []
        for skill in skills:
            annotations = _format_skill_annotations(skill)
            desc_line = f"- **{skill.get('name', '')}**: {skill.get('description', '')}"
            if annotations:
                desc_line += f" ({annotations})"
            lines.append(desc_line)

            allowed = skill.get("allowed_tools") or []
            if allowed:
                lines.append(
                    f"  -> Allowed tools: {', '.join(allowed)}"
                )

            # Key change from the base middleware: instead of telling the model
            # to read the file path directly, we direct it to use the load_skill
            # tool with the path as input.
            lines.append(
                "  -> To read this skill, call the `load_skill` tool with "
                f"the path `{skill.get('path', '')}`."
            )

        return "\n".join(lines)

