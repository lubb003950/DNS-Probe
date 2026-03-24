from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from packages.core.config import settings


def _engine_kwargs(database_url: str) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "future": True,
        "pool_pre_ping": True,
    }
    if database_url.startswith("sqlite"):
        return kwargs

    kwargs.update(
        {
            "pool_recycle": 3600,
            "pool_size": settings.db_pool_size,
            "max_overflow": settings.db_max_overflow,
        }
    )
    return kwargs


try:
    engine = create_engine(
        settings.database_url,
        **_engine_kwargs(settings.database_url),
    )
except ModuleNotFoundError as exc:
    if exc.name == "pymysql":
        raise RuntimeError(
            "pymysql is not installed. Run: pip install pymysql\n"
            "or: pip install -e ."
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
