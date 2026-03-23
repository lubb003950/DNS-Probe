from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from packages.core.config import settings


try:
    engine = create_engine(
        settings.database_url,
        future=True,
        pool_pre_ping=True,       # 自动检测断开的连接
        pool_recycle=3600,        # 每小时回收连接，防止 MySQL 的 wait_timeout 断连
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
except ModuleNotFoundError as exc:
    if exc.name == "pymysql":
        raise RuntimeError(
            "pymysql 未安装，请执行：pip install pymysql\n"
            "或：pip install -e ."
        ) from exc
    raise

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
