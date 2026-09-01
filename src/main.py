import os
import logging
from pathlib import Path
from typing import Optional, List, Dict
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.agent_orchestrator import (
    run_full_pipeline,
    get_snapshot,
    get_all_snapshots,
    get_chart_overall,
    get_chart_radar,
    get_chart_granularity,
    get_chart_waterfall,
    get_chart_risk,
    validate_round_limit,
    next_round_label,
    suggest_questions,
)
from services.job_manager import (
    create_job,
    submit_job,
    get_job,
    list_active_jobs,
    request_cancel,
    progress_callback_for,
    cancel_check_for,
    RoundLimitError,
)
from models.database import init_db
from services.paper_search_service import PaperSearchService
from services.question_service import get_science_questions

# 加载项目根目录的 .env
import sys
if getattr(sys, "frozen", False):
    _PROJECT_ROOT = Path(sys.executable).resolve().parent
    _BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", _PROJECT_ROOT))
else:
    _PROJECT_ROOT = Path(__file__).resolve().parent.parent
    _BUNDLE_DIR = _PROJECT_ROOT

load_dotenv(_PROJECT_ROOT / ".env")
# 建表（幂等，重复调用安全）
init_db()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Scientist 科学假设生成系统",
    description="基于多智能体协作的科学假设生成与迭代优化系统",
    version="1.0.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# UTF-8 强制编码中间件（防止中文被替换为 ? 的乱码问题）
# 1) 请求体：若 Content-Type 未指明 charset，强制按 UTF-8 重设；
# 2) 响应头：JSON 响应显式带 charset=utf-8；
# 3) 创建任务时打 question 调试日志（ord 前 5 字），快速定位乱码出现环节。
# ============================================================
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

class ForceUTF8Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # 强制请求按 UTF-8 解码
        ctype = request.headers.get("content-type", "")
        if ctype and "application/json" in ctype and "charset" not in ctype:
            # Starlette Request 内部用 charset 解码 body；这里不污染原始请求头，
            # 而是在 ASGI scope 里替换（否则再 build body 时仍用默认 charset）
            scope = request.scope
            new_headers = []
            for name, value in scope.get("headers", []):
                if name.decode("latin-1").lower() == "content-type":
                    new_headers.append(
                        (b"content-type", b"application/json; charset=utf-8")
                    )
                else:
                    new_headers.append((name, value))
            scope["headers"] = new_headers

        response = await call_next(request)

        # 响应头：FastAPI 默认 media_type="application/json"，我们补 charset
        ctype_out = response.headers.get("content-type", "")
        if ctype_out == "application/json":
            response.headers["content-type"] = "application/json; charset=utf-8"
        return response

app.add_middleware(ForceUTF8Middleware)

# 挂载前端静态文件（部署期使用，开发期建议用 dev server 反向代理）
# 优先用 exe/源码目录旁的 web/（方便替换），不存在则回退到打包内的 web/
_WEB_DIR = _PROJECT_ROOT / "web"
if not _WEB_DIR.exists() and _BUNDLE_DIR != _PROJECT_ROOT:
    _WEB_DIR = _BUNDLE_DIR / "web"
if _WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR)), name="static")


# ============================================================
# 请求/响应模型
# ============================================================

class RunRequest(BaseModel):
    question: str = Field(..., min_length=5, description="科学问题")
    feedback: Optional[str] = Field(None, description="专家反馈（迭代时传入）")
    initial_round: str = Field("V1", description="轮次标签")
    project_id: Optional[str] = Field(None, description="项目 ID（前端生成，缺省由后端生成）")
    auto_search_papers: bool = Field(False, description="是否在 pipeline 前自动检索 arXiv 文献入库（跑完清理，默认关闭，避免演示时在线下载 PDF 拖慢）")
    paper_granularity: str = Field("fast", description="arXiv 文献入库粒度：fast=摘要模式(省token,默认)，full=全文模式(证据更细)")
    images: Optional[List[Dict[str, str]]] = Field(None, description="多模态图片列表，每项含 name 和 data（base64 data URL）")
    documents: Optional[List[Dict[str, str]]] = Field(None, description="文档列表（PDF/Markdown），每项含 name 和 data（base64 data URL）")


class FeedbackRequest(BaseModel):
    question: str = Field(..., min_length=5)
    feedback: str = Field(..., min_length=3)
    current_round: str = Field(..., pattern=r"^V[1-3]$")
    project_id: Optional[str] = Field(None, description="项目 ID（前端生成，缺省由后端生成）")
    auto_search_papers: bool = Field(False, description="是否在 pipeline 前自动检索 arXiv 文献入库（跑完清理，默认关闭，避免演示时在线下载 PDF 拖慢）")
    paper_granularity: str = Field("fast", description="arXiv 文献入库粒度：fast=摘要模式(省token,默认)，full=全文模式(证据更细)")
    images: Optional[List[Dict[str, str]]] = Field(None, description="多模态图片列表，每项含 name 和 data（base64 data URL）")
    documents: Optional[List[Dict[str, str]]] = Field(None, description="文档列表（PDF/Markdown），每项含 name 和 data（base64 data URL）")


class SearchPapersRequest(BaseModel):
    """在线文献检索请求（基于 arXiv API）"""
    query: str = Field(..., min_length=2, description="搜索关键词（建议英文）")
    max_results: int = Field(5, ge=1, le=20, description="返回数")
    ingest: bool = Field(True, description="是否自动写入 Chroma（默认是）")
    dedupe: bool = Field(True, description="是否去重（默认是）")
    full_text: bool = Field(False, description="是否下载 PDF 全文并切分入库（默认只入库摘要）")


class SuggestQuestionsRequest(BaseModel):
    """迭代相关问题建议请求（输入框自动补全）"""
    context: str = Field("", description="用户已输入的前缀（可空），作为引导词让建议自然延伸")
    mode: str = Field("question", pattern=r"^(question|feedback)$", description="question=追问方向，feedback=改进建议")
    project_id: Optional[str] = Field(None, description="项目 ID（用于读取最近 snapshot）")
    top_k: int = Field(3, ge=1, le=5, description="生成数量（默认 3）")


class ChartResponse(BaseModel):
    success: bool
    data: Optional[dict] = None
    message: Optional[str] = None


# ============================================================
# API 接口
# ============================================================

@app.get("/")
async def root():
    """根路径"""
    return {
        "name": "AI Scientist",
        "version": "1.0.0",
        "status": "running",
        "model": os.getenv("QWEN_MODEL", "qwen-max")
    }


def _enqueue(question: str, round_label: str, feedback=None, project_id=None, auto_search_papers: bool = False, paper_granularity: str = "fast", images=None, documents=None):
    """创建后台任务并提交到线程池，返回 JobRecord。

    流水线在 worker 线程执行（不阻塞事件循环）；通过 progress_callback_for /
    cancel_check_for 闭包上报真实阶段、响应取消。
    """
    # 中文编码校验：若 question 中出现 U+FFFD（�）或多个 U+003F（?），记录告警并尝试用 question.encode/latin1 链路回退。
    # （用于快速定位乱码是出现在 HTTP 解析层还是数据库写入层）
    _ord_preview = [ord(c) for c in (question or "")[:8]]
    if "?" in (question or ""):
        logger.warning(
            f"[UTF-8 校验] question 含 '?' 字符，请检查上游编码：{question!r} (ord前8字={_ord_preview})"
        )
    else:
        logger.info(
            f"[UTF-8 校验] question 入队正常：{question!r} (ord前8字={_ord_preview})"
        )

    job = create_job(
        question=question,
        round_label=round_label,
        feedback=feedback,
        project_id=project_id,
    )

    def run_fn(job):
        return run_full_pipeline(
            question=job.question,
            feedback=job.feedback,
            round_label=job.round_label,
            project_id=job.project_id,
            progress_callback=progress_callback_for(job),
            cancel_check=cancel_check_for(job),
            auto_search_papers=auto_search_papers,
            paper_granularity=paper_granularity,
            images=images,
            documents=documents,
        )

    submit_job(job, run_fn)
    return job


@app.post("/api/run")
async def run_pipeline(request: RunRequest):
    """首次运行或带反馈重跑：后台执行，立即返回任务 ID。"""
    if request.feedback:
        try:
            validate_round_limit(request.initial_round)
        except RoundLimitError as e:
            raise HTTPException(status_code=400, detail=str(e))
        round_label = next_round_label(request.initial_round)
    else:
        round_label = request.initial_round
    job = _enqueue(
        question=request.question,
        round_label=round_label,
        feedback=request.feedback,
        project_id=request.project_id,
        auto_search_papers=request.auto_search_papers,
        paper_granularity=request.paper_granularity,
        images=request.images,
        documents=request.documents,
    )
    logger.info("已入队任务 %s（%s，%s，images=%d，documents=%d）", job.job_id, job.project_id, round_label, len(request.images) if request.images else 0, len(request.documents) if request.documents else 0)
    return {
        "success": True,
        "job_id": job.job_id,
        "project_id": job.project_id,
        "round_label": round_label,
        "status": job.status,
    }


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    """提交专家反馈触发迭代：后台执行，立即返回任务 ID。"""
    # 边界保护：V3 是最后一轮，不允许继续迭代生成 V4
    try:
        validate_round_limit(request.current_round)
    except RoundLimitError as e:
        raise HTTPException(status_code=400, detail=str(e))
    round_label = next_round_label(request.current_round)
    job = _enqueue(
        question=request.question,
        round_label=round_label,
        feedback=request.feedback,
        project_id=request.project_id,
        auto_search_papers=request.auto_search_papers,
        paper_granularity=request.paper_granularity,
        images=request.images,
        documents=request.documents,
    )
    logger.info("已入队迭代任务 %s（%s，%s，images=%d，documents=%d）", job.job_id, job.project_id, round_label, len(request.images) if request.images else 0, len(request.documents) if request.documents else 0)
    return {
        "success": True,
        "job_id": job.job_id,
        "project_id": job.project_id,
        "round_label": round_label,
        "status": job.status,
    }


@app.get("/api/job/{job_id}")
async def job_status(job_id: str):
    """查询任务状态（前端轮询真实阶段）"""
    data = get_job(job_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return {"success": True, "data": data}


@app.post("/api/job/{job_id}/cancel")
async def cancel_job(job_id: str):
    """请求取消任务（排队中立即取消；执行中在步骤边界中止）"""
    status = request_cancel(job_id)
    if status is None:
        raise HTTPException(status_code=404, detail=f"任务 {job_id} 不存在")
    return {"success": True, "status": status}


@app.get("/api/jobs")
async def list_jobs():
    """列出所有活跃任务（running / queued），供刷新后恢复轮询"""
    return {"success": True, "data": list_active_jobs()}


@app.get("/api/snapshot/{round_label}")
async def get_snapshot_api(round_label: str, project_id: Optional[str] = None):
    """获取指定版本快照"""
    data = get_snapshot(round_label, project_id=project_id)
    if not data:
        raise HTTPException(status_code=404, detail=f"快照 {round_label} 不存在")
    return {"success": True, "data": data}


@app.get("/api/snapshots")
async def list_snapshots(project_id: Optional[str] = None):
    """获取所有版本快照（可选按项目过滤）"""
    if project_id:
        data = get_all_snapshots()
        data = [s for s in data if s.get("project_id") == project_id]
    else:
        data = get_all_snapshots()
    return {"success": True, "data": data}


@app.post("/api/search-papers")
async def search_papers(request: SearchPapersRequest):
    """在线检索 arXiv 论文，可选自动写入 Chroma 向量库"""
    try:
        svc = PaperSearchService()
        if request.ingest:
            result = svc.search_and_ingest(
                query=request.query,
                max_results=request.max_results,
                dedupe=request.dedupe,
                full_text=request.full_text,
            )
            return {
                "success": True,
                "data": {
                    "query": result["query"],
                    "retrieved": result["retrieved"],
                    "ingested": result["ingested"],
                    "skipped": result["skipped"],
                    "mode": result["mode"],
                    "papers": result["papers"],
                },
            }
        else:
            # dry-run：仅检索不入库
            papers = svc.search(
                query=request.query,
                max_results=request.max_results,
            )
            return {
                "success": True,
                "data": {
                    "query": request.query,
                    "retrieved": len(papers),
                    "ingested": 0,
                    "skipped": 0,
                    "papers": [
                        {
                            "title": p.title,
                            "year": p.year,
                            "source": p.source,
                            "arxiv_id": p.arxiv_id,
                            "doi": p.doi,
                            "url": p.url,
                            "abstract": p.abstract[:300],
                        }
                        for p in papers
                    ],
                },
            }
    except Exception as e:
        logger.error(f"在线文献检索失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/questions")
async def questions_api():
    """从 Chroma 的 Science_125 文献中动态提取示例问题（前端「开始研究」弹窗用）。

    提取失败或向量库无该文献时返回空列表，前端回退到内置示例。
    """
    questions = get_science_questions()
    return {
        "success": True,
        "total": len(questions),
        "questions": [
            {
                "id": i + 1,
                "category": q["category"],
                "question": q["question"],
                "question_en": q.get("question_en", q["question"]),
            }
            for i, q in enumerate(questions)
        ],
    }


@app.post("/api/suggest-questions")
async def suggest_questions_api(request: SuggestQuestionsRequest):
    """生成 3 条迭代相关问题（输入框自动补全）。

    前端在输入框聚焦/输入时 debounce 调本接口，把返回的 questions
    以 chip 形式显示在输入栏上方，用户点击直接填入输入框。
    """
    result = suggest_questions(
        context=request.context,
        mode=request.mode,
        project_id=request.project_id,
        top_k=request.top_k,
    )
    return {
        "success": True,
        "questions": result.get("questions", []),
        "dims": result.get("dims", []),
        "based_on": result.get("based_on", "context_only"),
        "based_on_desc": result.get("based_on_desc", ""),
        "error": result.get("error"),
    }


@app.get("/api/chart/overall")
async def chart_overall(project_id: Optional[str] = None):
    """综合得分折线图"""
    return {"success": True, "data": get_chart_overall(project_id=project_id)}


@app.get("/api/chart/radar")
async def chart_radar(project_id: Optional[str] = None):
    """五维雷达图"""
    return {"success": True, "data": get_chart_radar(project_id=project_id)}


@app.get("/api/chart/granularity")
async def chart_granularity(project_id: Optional[str] = None):
    """计划颗粒度堆叠图"""
    return {"success": True, "data": get_chart_granularity(project_id=project_id)}


@app.get("/api/chart/waterfall")
async def chart_waterfall(project_id: Optional[str] = None):
    """缺陷修复瀑布图"""
    return {"success": True, "data": get_chart_waterfall(project_id=project_id)}


@app.get("/api/chart/risk")
async def chart_risk(project_id: Optional[str] = None):
    """反事实风险收敛图"""
    return {"success": True, "data": get_chart_risk(project_id=project_id)}


@app.get("/api/health")
async def health_check():
    """健康检查"""
    return {
        "status": "ok",
        "model": os.getenv("QWEN_MODEL", "qwen-max"),
        "api_key_configured": bool(os.getenv("DASHSCOPE_API_KEY"))
    }


PORT = int(os.getenv("PORT", "8848"))


def _ensure_knowledge_base() -> None:
    """首次启动自动初始化知识库：向量库为空时塞入内置种子文献（幂等，仅执行一次）。

    放在后台线程执行，不阻塞服务启动；失败仅告警，不影响服务。
    """
    if not os.getenv("DASHSCOPE_API_KEY"):
        logger.warning("未配置 DASHSCOPE_API_KEY，跳过知识库自动初始化（填入 Key 后重启即可）")
        return
    try:
        from services.chroma_service import ChromaService
        from config.seed_data import DEFAULT_SEED_DATA

        service = ChromaService()
        count = service.count_documents()
        if count == 0:
            logger.info("知识库为空，正在自动初始化内置种子文献（首次启动约 10-30 秒）…")
            service.add_documents(
                texts=[d["content"] for d in DEFAULT_SEED_DATA],
                metadatas=[{k: v for k, v in d.items() if k != "content"} for d in DEFAULT_SEED_DATA],
            )
            logger.info(f"知识库初始化完成，共 {len(DEFAULT_SEED_DATA)} 条文献")
        elif count > 0:
            logger.info(f"知识库已就绪（{count} 条文献），跳过初始化")
    except Exception as e:
        logger.warning(f"知识库自动初始化失败（不影响服务启动）: {e}")


def _open_browser() -> None:
    """服务就绪后自动打开系统页面（设置 AUTO_OPEN_BROWSER=0 可关闭）"""
    import threading
    import webbrowser

    if os.getenv("AUTO_OPEN_BROWSER", "1").strip().lower() in {"0", "false", "no"}:
        return

    def _open() -> None:
        import time
        time.sleep(3)  # 等服务完全起来
        try:
            webbrowser.open(f"http://127.0.0.1:{PORT}/static/index.html")
        except Exception as e:
            logger.warning(f"自动打开浏览器失败: {e}")

    threading.Thread(target=_open, daemon=True).start()


def _free_port(port: int) -> None:
    """启动前确保端口可用：Windows 下若端口被占用，直接结束占用进程。

    用法场景：重复双击 exe / 上次进程未退出，避免「端口被占用」启动失败。
    """
    import subprocess

    try:
        _res = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, timeout=10,
        )
        _raw = _res.stdout or b""
        # Windows 默认命令行编码可能是 GBK/CP936，并且输出头部可能含 BOM 类字节；
        # 这里优先按 utf-8 容错解码，失败则回退到 mbcs 再容错。
        for _enc in ("utf-8", "mbcs"):
            try:
                out = _raw.decode(_enc, errors="replace")
                break
            except Exception:
                out = ""
    except Exception:
        return  # 无法查询端口状态时不阻塞启动（uvicorn 会给出标准报错）

    pids = set()
    for line in (out or "").splitlines():
        if f":{port} " in line and "LISTENING" in line:
            parts = line.split()
            try:
                pid = int(parts[-1])
            except (ValueError, IndexError):
                continue
            if pid and pid != os.getpid():
                pids.add(pid)

    for pid in pids:
        logger.warning(f"端口 {port} 被 PID {pid} 占用，正在结束该进程…")
        try:
            subprocess.run(
                ["taskkill", "/F", "/PID", str(pid)],
                capture_output=True, text=True, timeout=15,
            )
            logger.info(f"PID {pid} 已结束")
        except Exception as kill_err:
            logger.warning(f"结束进程 {pid} 失败: {kill_err}")


if __name__ == "__main__":
    import threading
    import uvicorn

    _free_port(PORT)
    # 后台线程：首次启动自动初始化知识库 + 自动打开浏览器
    threading.Thread(target=_ensure_knowledge_base, daemon=True).start()
    _open_browser()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=PORT,
        log_level="info",
        # 单轮 V1 生成约 92s、V2 迭代约 117s，默认 5s keep-alive 会被断开
        timeout_keep_alive=300,
    )