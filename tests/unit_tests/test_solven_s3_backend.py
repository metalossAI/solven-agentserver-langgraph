"""Unit tests for SolvenS3Backend (config-driven S3 backend with company_id/threads path)."""
import pytest
from unittest.mock import patch, MagicMock

from src.backend import SolvenS3Backend


@patch("src.utils.config.get_workspace_id")
@patch("src.utils.config.get_user")
def test_solven_s3_backend_mounts_and_key(
    mock_get_user: MagicMock,
    mock_get_workspace_id: MagicMock,
) -> None:
    """SolvenS3Backend uses get_workspace_id(runtime) for path key; fallback is thread_id from config."""
    company_id = "company-uuid-123"
    thread_id = "thread-uuid-456"
    user_id = "user-uuid-789"
    mock_user = MagicMock()
    mock_user.id = user_id
    mock_user.company_id = company_id
    mock_get_user.return_value = mock_user
    mock_get_workspace_id.return_value = thread_id

    backend = SolvenS3Backend(runtime=None)

    assert backend.mounts["/workspace"] == f"{company_id}/threads/{thread_id}"
    assert backend.mounts["/ticket"] == f"{company_id}/threads/{thread_id}"
    assert backend.thread_id == thread_id
    assert backend.user_id == user_id
    assert backend._key("/workspace/foo") == f"{company_id}/threads/{thread_id}/foo"
    assert backend.id == f"s3-{thread_id}"


@patch("src.utils.config.get_workspace_id")
@patch("src.utils.config.get_user")
def test_solven_s3_backend_ticket_mount(
    mock_get_user: MagicMock,
    mock_get_workspace_id: MagicMock,
) -> None:
    """When workspace_id is set (e.g. from seleccionar_ticket), both /workspace and /ticket use it."""
    company_id = "company-uuid"
    ticket_id = "ticket-uuid"
    mock_user = MagicMock()
    mock_user.id = "user-id"
    mock_user.company_id = company_id
    mock_get_user.return_value = mock_user
    mock_get_workspace_id.return_value = ticket_id

    backend = SolvenS3Backend(runtime=MagicMock())

    assert backend.mounts["/workspace"] == f"{company_id}/threads/{ticket_id}"
    assert backend.mounts["/ticket"] == f"{company_id}/threads/{ticket_id}"


@patch("src.utils.config.get_workspace_id")
@patch("src.utils.config.get_user")
def test_solven_s3_backend_requires_thread_id(
    mock_get_user: MagicMock,
    mock_get_workspace_id: MagicMock,
) -> None:
    """SolvenS3Backend raises RuntimeError when workspace_id/thread_id is missing in config."""
    mock_get_user.return_value = MagicMock(company_id="company-1", id="user-1")
    mock_get_workspace_id.return_value = None

    with pytest.raises(RuntimeError, match="thread_id not found in config"):
        SolvenS3Backend(runtime=None)


@patch("src.utils.config.get_workspace_id")
@patch("src.utils.config.get_user")
def test_solven_s3_backend_requires_company_id(
    mock_get_user: MagicMock,
    mock_get_workspace_id: MagicMock,
) -> None:
    """SolvenS3Backend raises RuntimeError when user.company_id is missing."""
    mock_user = MagicMock()
    mock_user.company_id = None
    mock_user.id = "user-1"
    mock_get_user.return_value = mock_user
    mock_get_workspace_id.return_value = "thread-1"

    with pytest.raises(RuntimeError, match="company_id.*not found in config"):
        SolvenS3Backend(runtime=None)
