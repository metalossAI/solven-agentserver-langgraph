#!/usr/bin/env python3
"""
UNUSED in reliability-first sandbox design.
The sandbox_backend uses explicit sync boundaries only: after execution and uploads,
it runs rsync runtime -> thread store then rclone sync thread store -> S3. No
background daemon or FUSE at runtime.

Legacy description (paths differ from current layout):
- Watch /workspace/threads and /mnt/user-models, sync local <-> S3 via rclone.
- Local -> S3: debounced (1.5s) on watchdog; periodic upload (45s).
- S3 -> Local: periodic (10s) rclone sync.
- Env: S3_BUCKET, TENANT_ID, THREAD_ID, USER_ID, RCLONE_REMOTE.
- Run as systemd service (solven-sync.service) or via nohup.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileSystemEvent
except ImportError:
    print("watchdog not installed; install with: pip install watchdog", file=sys.stderr)
    sys.exit(1)

# Debounce delay (seconds) after last event before running rclone
DEBOUNCE_SEC = 1.5
# Periodic pull interval (seconds)
PERIODIC_INTERVAL_SEC = 10
# Periodic upload interval (seconds): safety net when inotify misses events
PERIODIC_UPLOAD_INTERVAL_SEC = 45
# Rclone sync timeout per run (seconds)
RCLONE_TIMEOUT = 300

WORKSPACE_BASE = "/workspace/threads"
USER_MODELS_PATH = "/mnt/user-models"


def env_required(key: str) -> str:
    v = os.environ.get(key, "").strip()
    if not v:
        print(f"Missing required env: {key}", file=sys.stderr)
        sys.exit(1)
    return v


def run_rclone_sync(local_path: str, remote_prefix: str, upload: bool, remote_name: str, bucket: str) -> bool:
    """Run rclone sync: if upload, local_path -> remote; else remote -> local_path."""
    remote = f"{remote_name}:{bucket}/{remote_prefix.rstrip('/')}"
    # #region agent log
    print(f"[sync_daemon] run_rclone_sync local_path={local_path!r} remote={remote!r} upload={upload} isdir={os.path.isdir(local_path)}", file=sys.stderr, flush=True)
    # #endregion
    if not os.path.isdir(local_path):
        print(f"[sync_daemon] skip (not a dir): {local_path}", file=sys.stderr, flush=True)
        return True
    try:
        if upload:
            # Do not persist env dirs to S3 to avoid excessive storage
            cmd = [
                "rclone", "sync", local_path, f"{remote}/",
                "--config", "/root/.config/rclone/rclone.conf",
                "--exclude", "node_modules/**",
                "--exclude", ".venv/**",
                "--exclude", ".bun/**",
                "--exclude", ".git/**",
            ]
        else:
            cmd = ["rclone", "sync", f"{remote}/", local_path, "--config", "/root/.config/rclone/rclone.conf"]
        result = subprocess.run(
            cmd,
            timeout=RCLONE_TIMEOUT,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"[sync_daemon] rclone failed exit={result.returncode} stderr={result.stderr!r} stdout={result.stdout!r}", file=sys.stderr, flush=True)
            return False
        print(f"[sync_daemon] rclone ok upload={upload} remote={remote!r}", file=sys.stderr, flush=True)
        return True
    except subprocess.TimeoutExpired as e:
        print(f"[sync_daemon] rclone timeout: {e}", file=sys.stderr, flush=True)
        return False


class DebouncedSyncHandler(FileSystemEventHandler):
    """Track dirty paths and trigger debounced rclone sync."""

    def __init__(
        self,
        bucket: str,
        tenant_id: str,
        thread_id: str,
        user_id: str,
        remote_name: str,
    ):
        self.bucket = bucket
        self.tenant_id = tenant_id
        self.thread_id = thread_id
        self.user_id = user_id
        self.remote_name = remote_name
        self._lock = threading.Lock()
        self._dirty_workspace_threads: set[str] = set()  # thread_id dirs that need sync
        self._dirty_user_models = False
        self._last_event_time = 0.0
        self._timer: threading.Timer | None = None

    def _schedule_flush(self) -> None:
        with self._lock:
            self._last_event_time = time.monotonic()
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(DEBOUNCE_SEC, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _path_to_workspace_thread(self, path: str) -> str | None:
        path = os.path.normpath(path)
        if not path.startswith(WORKSPACE_BASE + os.sep):
            return None
        rest = path[len(WORKSPACE_BASE) :].lstrip(os.sep)
        if not rest:
            return None
        return rest.split(os.sep)[0]

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = event.src_path
        if path.startswith(WORKSPACE_BASE + os.sep):
            thread_id = self._path_to_workspace_thread(path)
            if thread_id:
                # #region agent log
                print(f"[sync_daemon] event workspace path={path!r} -> thread_id={thread_id!r}", file=sys.stderr, flush=True)
                # #endregion
                with self._lock:
                    self._dirty_workspace_threads.add(thread_id)
                self._schedule_flush()
        elif path.startswith(USER_MODELS_PATH + os.sep) or path == USER_MODELS_PATH.rstrip(os.sep):
            with self._lock:
                self._dirty_user_models = True
            self._schedule_flush()

    def _flush(self) -> None:
        with self._lock:
            self._timer = None
            threads = set(self._dirty_workspace_threads)
            self._dirty_workspace_threads.clear()
            user_models = self._dirty_user_models
            self._dirty_user_models = False
        # #region agent log
        print(f"[sync_daemon] _flush threads={threads!r} user_models={user_models} tenant_id={self.tenant_id!r} bucket={self.bucket!r}", file=sys.stderr, flush=True)
        # #endregion
        for tid in threads:
            local = os.path.join(WORKSPACE_BASE, tid)
            prefix = f"{self.tenant_id}/threads/{tid}"
            run_rclone_sync(local, prefix, upload=True, remote_name=self.remote_name, bucket=self.bucket)
        if user_models:
            prefix = f"{self.tenant_id}/users/{self.user_id}/models"
            run_rclone_sync(USER_MODELS_PATH, prefix, upload=True, remote_name=self.remote_name, bucket=self.bucket)


def periodic_pull(bucket: str, tenant_id: str, thread_id: str, user_id: str, remote_name: str) -> None:
    """Run every PERIODIC_INTERVAL_SEC: S3 -> local for current thread and user-models."""
    while True:
        time.sleep(PERIODIC_INTERVAL_SEC)
        run_rclone_sync(
            os.path.join(WORKSPACE_BASE, thread_id),
            f"{tenant_id}/threads/{thread_id}",
            upload=False,
            remote_name=remote_name,
            bucket=bucket,
        )
        run_rclone_sync(
            USER_MODELS_PATH,
            f"{tenant_id}/users/{user_id}/models",
            upload=False,
            remote_name=remote_name,
            bucket=bucket,
        )


def periodic_upload(bucket: str, tenant_id: str, user_id: str, remote_name: str) -> None:
    """Run every PERIODIC_UPLOAD_INTERVAL_SEC: upload all thread dirs and user-models to S3. Safety net when inotify misses events."""
    while True:
        time.sleep(PERIODIC_UPLOAD_INTERVAL_SEC)
        if not os.path.isdir(WORKSPACE_BASE):
            continue
        try:
            for name in os.listdir(WORKSPACE_BASE):
                path = os.path.join(WORKSPACE_BASE, name)
                if os.path.isdir(path):
                    prefix = f"{tenant_id}/threads/{name}"
                    run_rclone_sync(path, prefix, upload=True, remote_name=remote_name, bucket=bucket)
            run_rclone_sync(
                USER_MODELS_PATH,
                f"{tenant_id}/users/{user_id}/models",
                upload=True,
                remote_name=remote_name,
                bucket=bucket,
            )
        except Exception as e:
            print(f"[sync_daemon] periodic_upload error: {e}", file=sys.stderr, flush=True)


def main() -> None:
    print("Solven sync daemon started (watchdog + rclone)", flush=True)
    bucket = env_required("S3_BUCKET")
    tenant_id = env_required("TENANT_ID")
    thread_id = env_required("THREAD_ID")
    user_id = env_required("USER_ID")
    remote_name = os.environ.get("RCLONE_REMOTE", "s3remote").strip()
    # #region agent log
    s3_prefix_thread = f"{tenant_id}/threads/{thread_id}"
    print(f"[sync_daemon] env bucket={bucket!r} tenant_id={tenant_id!r} thread_id={thread_id!r} S3_prefix_thread={s3_prefix_thread!r}", file=sys.stderr, flush=True)
    # #endregion

    for d in (WORKSPACE_BASE, USER_MODELS_PATH):
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)

    # Start periodic pull/upload once; only the observer is restarted on failure
    pull_thread = threading.Thread(
        target=periodic_pull,
        args=(bucket, tenant_id, thread_id, user_id, remote_name),
        daemon=True,
    )
    pull_thread.start()
    upload_thread = threading.Thread(
        target=periodic_upload,
        args=(bucket, tenant_id, user_id, remote_name),
        daemon=True,
    )
    upload_thread.start()

    while True:
        observer = None
        try:
            handler = DebouncedSyncHandler(
                bucket=bucket,
                tenant_id=tenant_id,
                thread_id=thread_id,
                user_id=user_id,
                remote_name=remote_name,
            )
            observer = Observer()
            observer.schedule(handler, WORKSPACE_BASE, recursive=True)
            observer.schedule(handler, USER_MODELS_PATH, recursive=True)
            observer.start()
            while observer.is_alive():
                observer.join(timeout=1.0)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"[sync_daemon] restart observer after error: {e}", file=sys.stderr, flush=True)
            time.sleep(5)
        finally:
            if observer is not None:
                try:
                    observer.stop()
                    observer.join(timeout=5.0)
                except Exception:
                    pass


if __name__ == "__main__":
    main()
