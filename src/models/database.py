"""
SQLAlchemy 数据库模型
用于存储快照、反馈、评分记录
"""

from sqlalchemy import (
    Column, String, Integer, Float, DateTime, JSON, Text,
    create_engine
)
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

Base = declarative_base()


# ============================================================
# 数据库路径解析（相对于项目根目录，避免 cwd 依赖）
# ============================================================

# 项目根目录 = src/ 的上一级
import sys
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

DATA_DIR = PROJECT_ROOT / "data"


def _resolve_database_url() -> str:
    """解析数据库 URL。

    优先级：
      1. 用户显式设置的 DATABASE_URL（非空）—— 原样使用，支持 sqlite / mysql+pymysql 等任意后端
      2. 用户填写了有效的 MYSQL_* 配置（密码不是占位值）—— 自动组装 mysql+pymysql://... URL
      3. 默认 SQLite，路径相对项目根目录而非 cwd
    """
    import urllib.parse

    url = (os.getenv("DATABASE_URL") or "").strip()

    # 情况 1：显式指定了 DATABASE_URL
    if url:
        # SQLite 相对路径：基于项目根目录解析为绝对路径，避免 cwd 依赖
        if url.startswith("sqlite:///"):
            db_path_str = url.replace("sqlite:///", "", 1)
            # Windows 绝对路径形如 C:/... 或 C:\...
            is_absolute = (
                len(db_path_str) > 1 and db_path_str[1] == ":"
            ) or db_path_str.startswith("/")
            if not is_absolute:
                db_path = (PROJECT_ROOT / db_path_str).resolve()
                db_path.parent.mkdir(parents=True, exist_ok=True)
                return f"sqlite:///{db_path.as_posix()}"
        return url

    # 情况 2：从 MYSQL_* 环境变量自动组装（pymysql 同步驱动，和 SQLAlchemy create_engine 配合）
    mysql_host = (os.getenv("MYSQL_HOST") or "").strip()
    mysql_port = (os.getenv("MYSQL_PORT") or "3306").strip()
    mysql_user = (os.getenv("MYSQL_USER") or "").strip()
    mysql_password = (os.getenv("MYSQL_PASSWORD") or "").strip()
    mysql_database = (os.getenv("MYSQL_DATABASE") or "").strip()

    is_mysql_configured = bool(
        mysql_host
        and mysql_user
        and mysql_database
        and mysql_password
        # 仅排除明确的占位值；"password"、"admin" 等可能是用户真实密码，不做黑名单拦截
        and mysql_password.lower() not in {"", "your_mysql_password", "changeme", "xxx"}
    )
    if is_mysql_configured:
        # URL 编码密码中的特殊字符（@ / : / % / ? 等）
        encoded_pwd = urllib.parse.quote_plus(mysql_password)
        return f"mysql+pymysql://{mysql_user}:{encoded_pwd}@{mysql_host}:{mysql_port}/{mysql_database}?charset=utf8mb4"

    # 情况 3：默认 SQLite（最稳妥，开箱即用）
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DATA_DIR / 'ai_scientist.db'}"


class SnapshotRecord(Base):
    """快照记录表"""
    __tablename__ = "snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    round = Column(String(10), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)
    question = Column(Text, nullable=False)
    overall_score = Column(Float, nullable=False)

    # 各 Agent 输出（JSON 序列化）
    explorer_output = Column(JSON, nullable=False)
    scientist_output = Column(JSON, nullable=False)
    critic_output = Column(JSON, nullable=False)

    # 颗粒度统计
    granularity_stats = Column(JSON, nullable=False)
    human_feedback = Column(JSON, nullable=True)  # 改为 nullable=True，由代码处理默认值


class FeedbackRecord(Base):
    """反馈记录表"""
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feedback_id = Column(String(36), nullable=False, unique=True)
    round = Column(String(10), nullable=False)
    target_agent = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    constraint_type = Column(String(20), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow)


# 数据库连接
DATABASE_URL = _resolve_database_url()
# SQLite 需要 check_same_thread=False（FastAPI 多线程场景）
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    """初始化数据库（创建所有表）"""
    Base.metadata.create_all(bind=engine)