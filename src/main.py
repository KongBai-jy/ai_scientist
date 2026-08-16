"""
FastAPI 服务入口
提供 REST API 供前端调用
"""

import os
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.agent_orchestrator import (
    run_full_pipeline,
    iterate_with_feedback,
    get_snapshot,
    get_all_snapshots,
    get_chart_overall,
    get_chart_radar,
    get_chart_granularity,
    get_chart_waterfall,
    get_chart_risk,
)
from models.database import init_db

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


class FeedbackRequest(BaseModel):
    question: str = Field(..., min_length=5)
    feedback: str = Field(..., min_length=3)
    current_round: str = Field(..., pattern=r"^V[1-3]$")


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


@app.post("/api/run")
async def run_pipeline(request: RunRequest):
    """首次运行或带反馈重跑"""
    try:
        if request.feedback:
            # 带反馈迭代
            result = iterate_with_feedback(
                question=request.question,
                feedback=request.feedback,
                current_round=request.initial_round
            )
        else:
            # 首次运行
            result = run_full_pipeline(
                question=request.question,
                round_label=request.initial_round
            )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"运行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/feedback")
async def submit_feedback(request: FeedbackRequest):
    """提交专家反馈触发迭代"""
    # 边界保护：V3 是最后一轮，不允许继续迭代生成 V4
    if request.current_round == "V3":
        raise HTTPException(
            status_code=400,
            detail="已到最大迭代次数 V3，无法继续迭代"
        )
    try:
        result = iterate_with_feedback(
            question=request.question,
            feedback=request.feedback,
            current_round=request.current_round
        )
        return {"success": True, "data": result}
    except HTTPException:
        raise  # 保留上面的 400 边界错误
    except Exception as e:
        logger.error(f"反馈处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/snapshot/{round_label}")
async def get_snapshot_api(round_label: str):
    """获取指定版本快照"""
    data = get_snapshot(round_label)
    if not data:
        raise HTTPException(status_code=404, detail=f"快照 {round_label} 不存在")
    return {"success": True, "data": data}


@app.get("/api/snapshots")
async def list_snapshots():
    """获取所有版本快照"""
    data = get_all_snapshots()
    return {"success": True, "data": data}


@app.get("/api/chart/overall")
async def chart_overall():
    """综合得分折线图"""
    return {"success": True, "data": get_chart_overall()}


@app.get("/api/chart/radar")
async def chart_radar():
    """五维雷达图"""
    return {"success": True, "data": get_chart_radar()}


@app.get("/api/chart/granularity")
async def chart_granularity():
    """计划颗粒度堆叠图"""
    return {"success": True, "data": get_chart_granularity()}


@app.get("/api/chart/waterfall")
async def chart_waterfall():
    """缺陷修复瀑布图"""
    return {"success": True, "data": get_chart_waterfall()}


@app.get("/api/chart/risk")
async def chart_risk():
    """反事实风险收敛图"""
    return {"success": True, "data": get_chart_risk()}


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
        out = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return  # 无法查询端口状态时不阻塞启动（uvicorn 会给出标准报错）

    pids = set()
    for line in out.splitlines():
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