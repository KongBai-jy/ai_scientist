"""
后台任务管理器（Job Manager）

将同步阻塞的科研流水线改造成「提交任务 → 后台线程池执行 → 前端轮询状态」。
- 并发上限 MAX_CONCURRENT_JOBS = 2，超出的任务在 ThreadPoolExecutor 内部队列排队。
- 每个任务记录真实执行阶段（explorer / scientist / critic），供前端轮询展示。
- 支持真正的中止：流水线在步骤边界检查 cancel 标志并抛 PipelineCancelled。

本模块不依赖 FastAPI，可独立单测。
"""

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_CONCURRENT_JOBS = 2

# 任务状态机
STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

# 执行阶段
STAGE_EXPLORER = "explorer"
STAGE_SCIENTIST = "scientist"
STAGE_CRITIC = "critic"


class PipelineCancelled(Exception):
    """流水线被用户取消时抛出（在步骤边界检查到 cancel 标志）。"""


class RoundLimitError(Exception):
    """已到达最大迭代轮次，无法继续迭代。"""


@dataclass
class JobRecord:
    job_id: str
    project_id: str
    question: str
    round_label: str            # 本轮正在生成的轮次，如 "V1"/"V2"/"V3"
    feedback: Optional[str] = None
    status: str = STATUS_QUEUED
    stage: Optional[str] = None     # explorer | scientist | critic | None
    progress: Optional[int] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: str = ""
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    cancel_requested: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("cancel_requested", None)
        return d


_JOBS: Dict[str, JobRecord] = {}
_JOBS_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_JOBS,
    thread_name_prefix="aisci",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(
    question: str,
    round_label: str,
    feedback: Optional[str] = None,
    project_id: Optional[str] = None,
) -> JobRecord:
    """创建排队中的任务记录（不执行）。project_id 缺省时生成新 UUID。"""
    job_id = uuid.uuid4().hex
    if not project_id:
        project_id = uuid.uuid4().hex
    job = JobRecord(
        job_id=job_id,
        project_id=project_id,
        question=question,
        round_label=round_label,
        feedback=feedback,
        created_at=_now(),
    )
    with _JOBS_LOCK:
        _JOBS[job_id] = job
    return job


def submit_job(job: JobRecord, run_fn: Callable[[JobRecord], Dict[str, Any]]) -> None:
    """把任务提交到线程池执行。run_fn(job) 执行流水线并返回快照 dict。

    _run 包装负责状态机流转（queued→running→done/error/cancelled）。
    """

    def _run() -> None:
        with _JOBS_LOCK:
            if job.cancel_requested:
                job.status = STATUS_CANCELLED
                job.finished_at = _now()
                return
            job.status = STATUS_RUNNING
            job.started_at = _now()
        try:
            result = run_fn(job)
            with _JOBS_LOCK:
                job.status = STATUS_DONE
                job.result = result
                job.stage = None
                job.finished_at = _now()
        except PipelineCancelled:
            with _JOBS_LOCK:
                job.status = STATUS_CANCELLED
                job.stage = None
                job.finished_at = _now()
        except Exception as e:  # noqa: BLE001 - 任何异常都收进任务状态
            logger.exception("任务 %s 执行失败", job.job_id)
            with _JOBS_LOCK:
                job.status = STATUS_ERROR
                job.error = str(e)
                job.stage = None
                job.finished_at = _now()

    _EXECUTOR.submit(_run)


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return job.to_dict() if job else None


def list_active_jobs() -> List[Dict[str, Any]]:
    """返回仍在执行或排队的任务列表。"""
    with _JOBS_LOCK:
        return [
            j.to_dict()
            for j in _JOBS.values()
            if j.status in (STATUS_QUEUED, STATUS_RUNNING)
        ]


def request_cancel(job_id: str) -> Optional[str]:
    """请求取消任务。排队中的任务立即标记取消；执行中的置标志，由流水线在步骤边界响应。

    返回取消后状态；任务不存在返回 None。
    """
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return None
        job.cancel_requested = True
        if job.status == STATUS_QUEUED:
            job.status = STATUS_CANCELLED
            job.finished_at = _now()
        return job.status


def progress_callback_for(job: JobRecord) -> Callable[[Optional[str], Optional[int]], None]:
    """生成进度上报闭包：任务内更新 job.stage / job.progress。"""
    def _cb(stage: Optional[str], progress: Optional[int] = None) -> None:
        with _JOBS_LOCK:
            job.stage = stage
            job.progress = progress
    return _cb


def cancel_check_for(job: JobRecord) -> Callable[[], bool]:
    """生成取消检查闭包：流水线在步骤边界轮询。"""
    def _cc() -> bool:
        with _JOBS_LOCK:
            return job.cancel_requested
    return _cc
