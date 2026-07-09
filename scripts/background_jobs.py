import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
VALID_STATUSES = {"queued", "running", *TERMINAL_STATUSES}
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class BackgroundJobCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class BackgroundJobStatus:
    job_id: str
    name: str
    status: str
    stage: str | None = None
    message: str | None = None
    error: str | None = None
    log_path: str | None = None
    cancel_file: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "name": self.name,
            "status": self.status,
            "stage": self.stage,
            "message": self.message,
            "error": self.error,
            "log_path": self.log_path,
            "cancel_file": self.cancel_file,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
        }


class BackgroundJobContext:
    def __init__(self, job_id: str, cancel_event: threading.Event, cancel_file: Path, update_status: Callable[..., None]):
        self.job_id = job_id
        self.cancel_event = cancel_event
        self.cancel_file = cancel_file
        self._update_status = update_status

    def update(self, stage: str | None = None, message: str | None = None) -> None:
        self._update_status(stage=stage, message=message)

    def is_cancel_requested(self) -> bool:
        return self.cancel_event.is_set() or self.cancel_file.exists()

    def raise_if_cancelled(self) -> None:
        if self.is_cancel_requested():
            raise BackgroundJobCancelled("background job cancelled")


class _BackgroundJobRecord:
    def __init__(self, status: BackgroundJobStatus, cancel_event: threading.Event):
        self.status = status
        self.cancel_event = cancel_event
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen[str] | None = None


class BackgroundJobRegistry:
    def __init__(self, root: Path | str, log_dir: Path | str | None = None, cancel_dir: Path | str | None = None):
        self.root = Path(root)
        self.log_dir = Path(log_dir) if log_dir is not None else self.root / "logs" / "background_jobs"
        self.cancel_dir = Path(cancel_dir) if cancel_dir is not None else self.root / "logs" / "background_job_cancel"
        self._lock = threading.Lock()
        self._jobs: dict[str, _BackgroundJobRecord] = {}

    def start_function(
        self,
        name: str,
        func: Callable[[BackgroundJobContext], Any],
        *,
        job_id: str | None = None,
        log_path: Path | str | None = None,
    ) -> BackgroundJobStatus:
        job_id = self._new_job_id(job_id)
        log_file = self._prepare_log_path(job_id, log_path)
        cancel_file = self._cancel_file(job_id)
        record = self._create_record(job_id=job_id, name=name, log_path=log_file, cancel_file=cancel_file)

        def run() -> None:
            self._mark_running(job_id, stage="function", message="started")
            context = BackgroundJobContext(
                job_id=job_id,
                cancel_event=record.cancel_event,
                cancel_file=cancel_file,
                update_status=lambda **kwargs: self._update_status(job_id, **kwargs),
            )
            try:
                context.raise_if_cancelled()
                func(context)
                if context.is_cancel_requested():
                    self._finish(job_id, status="cancelled", message="cancelled")
                else:
                    self._finish(job_id, status="succeeded", message="completed")
            except BackgroundJobCancelled as exc:
                self._finish(job_id, status="cancelled", message=str(exc))
            except Exception as exc:
                self._append_log(log_file, f"{type(exc).__name__}: {exc}\n")
                self._finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")

        self._start_thread(record, name, run)
        return self.get_status(job_id)

    def start_subprocess(
        self,
        name: str,
        command: list[str],
        *,
        job_id: str | None = None,
        cwd: Path | str | None = None,
        env: dict[str, str] | None = None,
        log_path: Path | str | None = None,
    ) -> BackgroundJobStatus:
        job_id = self._new_job_id(job_id)
        log_file = self._prepare_log_path(job_id, log_path)
        cancel_file = self._cancel_file(job_id)
        record = self._create_record(job_id=job_id, name=name, log_path=log_file, cancel_file=cancel_file)

        def run() -> None:
            self._mark_running(job_id, stage="subprocess", message="started")
            try:
                with log_file.open("a", encoding="utf-8") as log_handle:
                    process = subprocess.Popen(
                        command,
                        cwd=str(cwd) if cwd is not None else None,
                        env={**os.environ, **env} if env is not None else None,
                        stdout=log_handle,
                        stderr=subprocess.STDOUT,
                        text=True,
                    )
                    record.process = process
                    while True:
                        returncode = process.poll()
                        if returncode is not None:
                            if record.cancel_event.is_set() or cancel_file.exists():
                                self._finish(job_id, status="cancelled", message="cancelled", returncode=returncode)
                            elif returncode == 0:
                                self._finish(job_id, status="succeeded", message="completed", returncode=returncode)
                            else:
                                self._finish(
                                    job_id,
                                    status="failed",
                                    error=f"process exited with code {returncode}",
                                    returncode=returncode,
                                )
                            return
                        if record.cancel_event.is_set() or cancel_file.exists():
                            self._terminate_process(process)
                        record.cancel_event.wait(0.05)
            except Exception as exc:
                self._append_log(log_file, f"{type(exc).__name__}: {exc}\n")
                self._finish(job_id, status="failed", error=f"{type(exc).__name__}: {exc}")

        self._start_thread(record, name, run)
        return self.get_status(job_id)

    def get_status(self, job_id: str) -> BackgroundJobStatus:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"unknown background job: {job_id}")
            return self._jobs[job_id].status

    def request_cancel(self, job_id: str) -> BackgroundJobStatus:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(f"unknown background job: {job_id}")
            record = self._jobs[job_id]
            if record.status.status in TERMINAL_STATUSES:
                return record.status
            record.cancel_event.set()
            cancel_file = Path(record.status.cancel_file) if record.status.cancel_file else self._cancel_file(job_id)
            cancel_file.parent.mkdir(parents=True, exist_ok=True)
            cancel_file.write_text(_now_iso(), encoding="utf-8")
            if record.process is not None and record.process.poll() is None:
                self._terminate_process(record.process)
            record.status = self._replace_status(record.status, message="cancel requested")
            return record.status

    def _create_record(self, job_id: str, name: str, log_path: Path, cancel_file: Path) -> _BackgroundJobRecord:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.cancel_dir.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
        status = BackgroundJobStatus(
            job_id=job_id,
            name=name,
            status="queued",
            message="queued",
            log_path=str(log_path),
            cancel_file=str(cancel_file),
            created_at=_now_iso(),
        )
        record = _BackgroundJobRecord(status=status, cancel_event=threading.Event())
        with self._lock:
            self._jobs[job_id] = record
        return record

    def _start_thread(self, record: _BackgroundJobRecord, name: str, target: Callable[[], None]) -> None:
        thread = threading.Thread(target=target, name=f"background-job-{name}", daemon=True)
        record.thread = thread
        thread.start()

    def _mark_running(self, job_id: str, stage: str, message: str) -> None:
        self._update_status(job_id, status="running", stage=stage, message=message, started_at=_now_iso())

    def _finish(
        self,
        job_id: str,
        *,
        status: str,
        message: str | None = None,
        error: str | None = None,
        returncode: int | None = None,
    ) -> None:
        self._update_status(
            job_id,
            status=status,
            message=message,
            error=error,
            finished_at=_now_iso(),
            returncode=returncode,
        )

    def _update_status(self, job_id: str, **changes: Any) -> None:
        status = changes.get("status")
        if status is not None and status not in VALID_STATUSES:
            raise ValueError(f"invalid background job status: {status}")
        with self._lock:
            record = self._jobs[job_id]
            record.status = self._replace_status(record.status, **changes)

    def _replace_status(self, current: BackgroundJobStatus, **changes: Any) -> BackgroundJobStatus:
        values = current.to_dict()
        values.update({key: value for key, value in changes.items() if value is not None})
        return BackgroundJobStatus(**values)

    def _prepare_log_path(self, job_id: str, log_path: Path | str | None) -> Path:
        path = Path(log_path) if log_path is not None else self.log_dir / f"{job_id}.log"
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        log_root = self.log_dir.resolve()
        try:
            path.relative_to(log_root)
        except ValueError as exc:
            raise ValueError("background job log_path must stay inside the registry log directory.") from exc
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _cancel_file(self, job_id: str) -> Path:
        return self.cancel_dir / f"{job_id}.cancel"

    def _new_job_id(self, job_id: str | None) -> str:
        if job_id:
            if not JOB_ID_PATTERN.fullmatch(job_id):
                raise ValueError("background job id contains invalid characters.")
            return job_id
        return uuid.uuid4().hex

    def _append_log(self, log_path: Path, text: str) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(text)

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
