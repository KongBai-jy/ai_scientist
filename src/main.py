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

# 加载项目根目录的 .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
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
_WEB_DIR = _PROJECT_ROOT / "web"
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
        # 单轮 V1 生成约 92s、V2 迭代约 117s，默认 5s keep-alive 会被断开
        timeout_keep_alive=300,
    )