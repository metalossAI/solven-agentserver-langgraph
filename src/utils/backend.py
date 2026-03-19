"""Resolve the active file backend (sandbox vs S3) from LangGraph config."""

from __future__ import annotations

from typing import Any

from langchain.tools import ToolRuntime
from langgraph.config import get_config

from src.models import AppContext

# Process-level SandboxBackend cache keyed by thread_id (same semantics as former sandbox_backend module).
_BACKEND_INSTANCES: dict[str, Any] = {}
_BACKEND_INSTANCE_ORDER: list[str] = []
_BACKEND_INSTANCES_MAX = 256


def get_backend(runtime: ToolRuntime[AppContext]):
	"""Return the file/backend for this runtime (SandboxBackend or SolvenS3Backend).

	Chosen by configurable ``backend`` (frontend / assistant UI):
	- ``backend == "s3"`` -> SolvenS3Backend (file-only).
	- ``backend == "sandbox"`` or missing -> SandboxBackend (cached per thread_id).

	Callers should pass logical paths like ``/adjuntos/...`` to ``aupload_files`` / ``adownload_files``;
	each backend resolves them internally.

	SolvenS3Backend and SandboxBackend are imported inside this function to avoid circular imports
	with ``src.backend`` / ``src.sandbox_backend``.
	"""
	from src.utils.config import get_workspace_id

	config = get_config()
	configurable = config.get("configurable") or {}
	backend_param = (configurable.get("backend") or "").strip().lower()
	if backend_param == "s3":
		from src.backend import SolvenS3Backend
		return SolvenS3Backend(runtime)

	from src.sandbox_backend import SandboxBackend

	thread_id = get_workspace_id(runtime)
	if thread_id and thread_id in _BACKEND_INSTANCES:
		return _BACKEND_INSTANCES[thread_id]

	backend = SandboxBackend(runtime)
	if thread_id:
		while len(_BACKEND_INSTANCES) >= _BACKEND_INSTANCES_MAX and _BACKEND_INSTANCE_ORDER:
			oldest = _BACKEND_INSTANCE_ORDER.pop(0)
			_BACKEND_INSTANCES.pop(oldest, None)
		_BACKEND_INSTANCES[thread_id] = backend
		_BACKEND_INSTANCE_ORDER.append(thread_id)
	return backend


def invalidate_backend(thread_id: str) -> None:
	"""Drop cached SandboxBackend for thread_id (e.g. stale E2B). Next get_backend() creates a new one."""
	_BACKEND_INSTANCES.pop(thread_id, None)
	try:
		_BACKEND_INSTANCE_ORDER.remove(thread_id)
	except ValueError:
		pass
