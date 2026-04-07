from __future__ import annotations
import logging
import threading
import uuid
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from compact_middleware.tokens import token_count_with_estimation
from langsmith import AsyncClient
from langsmith.utils import LangSmithError
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    dynamic_prompt,
)
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deepagents.middleware.skills import (
    SkillsMiddleware as BaseSkillsMiddleware,
    SkillMetadata,
    SkillsState,
    SkillsStateUpdate,
    _alist_skills,
    _list_skills,
)

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model

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

# additional_kwargs on injected SystemMessage — UI may filter on this key
LC_AUTO_EVALUATION_KWARG = "lc_auto_evaluation"

DEFAULT_PERIODIC_AUTO_EVALUATION_PROMPT = (
    "Pausa de autoevaluación: revisa el hilo reciente (últimas herramientas y sus resultados). "
    "Comprueba si la dirección general es correcta, si los resultados son coherentes con la petición "
    "del usuario y si conviene corregir el plan o repetir alguna herramienta con otros parámetros. "
    "Si todo es correcto, continúa con más herramientas o con tu respuesta final; si no, ajusta el curso."
)

DEFAULT_REPETITION_ESCALATION_PROMPT = (
    "Alerta de autoevaluación (patrón repetitivo): has usado la misma herramienta varias veces seguidas "
    "con poco progreso. Detente: no repitas el mismo enfoque. Si falta información o el archivo no existe, "
    "usa la herramienta ``ask`` para preguntar al usuario o cambia de estrategia por completo "
    "(otra ruta, otro directorio, o admite que no puedes continuar sin datos)."
)

# Mirrors compact_middleware.config.ContextSize
ContextSize = tuple[str, int | float]


@dataclass
class AutoevaluationConfig:
    """Trigger semantics align with compact-middleware where possible; adds delta_* since last injection."""

    trigger: ContextSize | list[ContextSize] = field(
        default_factory=lambda: ("delta_tokens", 10_000),
    )
    prompt: str = DEFAULT_PERIODIC_AUTO_EVALUATION_PROMPT
    repetition_bypass: bool = True
    repetition_tool_window: int = 10
    repetition_threshold: int = 3
    repetition_prompt: str = DEFAULT_REPETITION_ESCALATION_PROMPT
    model_context_size: int | None = None


@dataclass
class _EvalCheckpoint:
    last_eval_tokens: int = 0
    last_eval_msg_index: int = 0


def _eval_should_trigger(
    *,
    total_tokens: int,
    delta_tokens: int,
    num_messages: int,
    delta_messages: int,
    trigger: ContextSize | list[ContextSize],
    max_input_tokens: int | None,
) -> bool:
    conditions = trigger if isinstance(trigger, list) else [trigger]
    for kind, value in conditions:
        if kind == "delta_tokens" and delta_tokens >= int(value):
            return True
        if kind == "delta_messages" and delta_messages >= int(value):
            return True
        if kind == "tokens" and total_tokens >= int(value):
            return True
        if kind == "messages" and num_messages >= int(value):
            return True
        if kind == "fraction" and max_input_tokens is not None:
            threshold = int(max_input_tokens * float(value))
            if total_tokens >= max(1, threshold):
                return True
    return False


def _repetition_exceeds(
    messages: list[Any],
    *,
    window_messages: int,
    threshold: int,
) -> bool:
    if threshold < 2 or window_messages < 1:
        return False
    recent = messages[-window_messages:] if len(messages) > window_messages else messages
    names: list[str] = []
    for msg in recent:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                n = (tc.get("name") or "").strip()
                if n:
                    names.append(n)
    if not names:
        return False
    counts = Counter(names)
    return max(counts.values()) >= threshold


def _max_input_tokens_from_model(model: Any) -> int | None:
    profile = getattr(model, "profile", None)
    if isinstance(profile, dict):
        v = profile.get("max_input_tokens")
        if isinstance(v, int):
            return v
    return None


class AutoevaluationMiddleware(AgentMiddleware):
    """Inject periodic autoevaluation into the system prompt using hybrid token counts (compact-middleware).

    Checkpoint state is stored per thread on the middleware instance (LangChain 1.2.x does not apply
    ``Command(update=...)`` from middleware reliably).
    """

    def __init__(
        self,
        model: Any | None = None,
        *,
        config: AutoevaluationConfig | None = None,
    ) -> None:
        super().__init__()
        self._model = model
        self._config = config or AutoevaluationConfig()
        self._lock = threading.Lock()
        self._thread_states: dict[str, _EvalCheckpoint] = {}
        self._fallback_thread_id = f"autoeval_session_{uuid.uuid4().hex[:12]}"

    def _get_thread_id(self) -> str:
        try:
            cfg = get_config()
            tid = cfg.get("configurable", {}).get("thread_id")
            if tid is not None:
                return str(tid)
        except RuntimeError:
            pass
        return self._fallback_thread_id

    def _get_checkpoint(self, thread_id: str) -> _EvalCheckpoint:
        with self._lock:
            if thread_id not in self._thread_states:
                self._thread_states[thread_id] = _EvalCheckpoint()
            return self._thread_states[thread_id]

    def _update_checkpoint(self, thread_id: str, total_tokens: int, msg_index: int) -> None:
        with self._lock:
            self._thread_states[thread_id] = _EvalCheckpoint(
                last_eval_tokens=total_tokens,
                last_eval_msg_index=msg_index,
            )

    def _max_context(self) -> int | None:
        if self._config.model_context_size is not None:
            return self._config.model_context_size
        if self._model is not None:
            return _max_input_tokens_from_model(self._model)
        return None

    def _with_eval_system(self, request: ModelRequest, text: str) -> ModelRequest:
        sm = request.system_message
        prior_blocks = list(sm.content_blocks) if sm is not None else []
        block: dict[str, Any] = {"type": "text", "text": text}
        new_content = prior_blocks + [block]
        return request.override(
            system_message=SystemMessage(
                content=new_content,
                additional_kwargs={LC_AUTO_EVALUATION_KWARG: True},
            ),
        )

    def _run_gate(self, request: ModelRequest) -> tuple[bool, str | None]:
        messages = list(request.messages or [])
        system = request.system_message
        counted: list[Any] = [system, *messages] if system is not None else messages
        current_tokens = token_count_with_estimation(counted)
        thread_id = self._get_thread_id()
        ck = self._get_checkpoint(thread_id)
        delta_tokens = current_tokens - ck.last_eval_tokens
        delta_messages = len(messages) - ck.last_eval_msg_index
        max_in = self._max_context()

        token_ok = _eval_should_trigger(
            total_tokens=current_tokens,
            delta_tokens=delta_tokens,
            num_messages=len(messages),
            delta_messages=delta_messages,
            trigger=self._config.trigger,
            max_input_tokens=max_in,
        )
        if token_ok:
            return True, self._config.prompt
        if self._config.repetition_bypass and _repetition_exceeds(
            messages,
            window_messages=self._config.repetition_tool_window,
            threshold=self._config.repetition_threshold,
        ):
            return True, self._config.repetition_prompt
        return False, None

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        try:
            inject, prompt = self._run_gate(request)
            if inject and prompt:
                thread_id = self._get_thread_id()
                messages = list(request.messages or [])
                system = request.system_message
                counted: list[Any] = [system, *messages] if system is not None else messages
                current_tokens = token_count_with_estimation(counted)
                request = self._with_eval_system(request, prompt)
                self._update_checkpoint(thread_id, current_tokens, len(messages))
        except Exception:
            logging.exception("AutoevaluationMiddleware.wrap_model_call failed; continuing without injection.")
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            inject, prompt = self._run_gate(request)
            if inject and prompt:
                thread_id = self._get_thread_id()
                messages = list(request.messages or [])
                system = request.system_message
                counted: list[Any] = [system, *messages] if system is not None else messages
                current_tokens = token_count_with_estimation(counted)
                request = self._with_eval_system(request, prompt)
                self._update_checkpoint(thread_id, current_tokens, len(messages))
        except Exception:
            logging.exception("AutoevaluationMiddleware.awrap_model_call failed; continuing without injection.")
        return await handler(request)


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
    SkillsMiddleware for Solven: extends the default deepagents skills list formatting
    with optional exclude_skills (filter by name or path, e.g. ["docx"]).
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
        """Format skills for the system prompt; applies exclude_skills then delegates to base."""
        return super()._format_skills_list(self._filtered_skills(skills))

    def before_agent(
        self,
        state: SkillsState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> SkillsStateUpdate | None:
        """Like base SkillsMiddleware, but do not skip reload when skills_metadata is []."""
        if state.get("skills_metadata"):
            return None
        backend = self._get_backend(state, runtime, config)
        all_skills: dict[str, SkillMetadata] = {}
        for source_path in self.sources:
            for skill in _list_skills(backend, source_path):
                all_skills[skill["name"]] = skill
        return SkillsStateUpdate(skills_metadata=list(all_skills.values()))

    async def abefore_agent(
        self,
        state: SkillsState,
        runtime: Runtime,
        config: RunnableConfig,
    ) -> SkillsStateUpdate | None:
        if state.get("skills_metadata"):
            return None
        backend = self._get_backend(state, runtime, config)
        all_skills: dict[str, SkillMetadata] = {}
        for source_path in self.sources:
            for skill in await _alist_skills(backend, source_path):
                all_skills[skill["name"]] = skill
        return SkillsStateUpdate(skills_metadata=list(all_skills.values()))
