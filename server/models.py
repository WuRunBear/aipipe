"""SQLite/SQLAlchemy 数据模型（M3：pipelines / runs / step_runs / settings）。"""
import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .config import DB_URL

engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class Base(DeclarativeBase):
    pass


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    description: Mapped[str] = mapped_column(String, default="")
    manifest_json: Mapped[str] = mapped_column(Text)  # 原样 pipeline.yaml 内容
    image: Mapped[str] = mapped_column(String, default="")  # 解析出的镜像名
    source_dir: Mapped[str] = mapped_column(String)  # 流水线目录绝对路径
    status: Mapped[str] = mapped_column(String, default="active")  # active/disabled
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    pipeline_id: Mapped[int] = mapped_column(ForeignKey("pipelines.id"), index=True)
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String, default="queued")  # queued/running/success/failed
    current_step: Mapped[int] = mapped_column(Integer, default=0)  # 1-based，0=未开始
    error: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    step_index: Mapped[int] = mapped_column(Integer)  # 1-based
    step_name: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued/running/success/failed
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_path: Mapped[str] = mapped_column(String, default="")
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)


class Setting(Base):
    """单行配置（PRD §5 settings）：Webhook URL、登录密码哈希。"""
    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # 恒为 1
    webhook_url: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str] = mapped_column(String, default="")  # bcrypt；空=未初始化


def get_settings(session) -> Setting:
    """取单行 settings，不存在则创建。"""
    s = session.get(Setting, 1)
    if s is None:
        s = Setting(id=1)
        session.add(s)
        session.commit()
    return s


def init_db() -> None:
    Base.metadata.create_all(engine)
