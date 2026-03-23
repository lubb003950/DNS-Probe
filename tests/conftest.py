"""
测试环境配置：用共享内存 SQLite 覆盖数据库，
避免测试依赖真实 MySQL 服务。
"""
from __future__ import annotations

import os

# 必须在所有业务模块导入之前设置，确保 Settings 读到正确值
os.environ["DNS_PROBE_DATABASE_URL"] = "sqlite:///:memory:"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import packages.db.session as db_session
from packages.db.models import Base


@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    """
    用 StaticPool 内存 SQLite 替换引擎，使所有连接共享同一个库，
    并在测试开始前建表，结束后清理。
    """
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_session.engine = test_engine
    db_session.SessionLocal = sessionmaker(
        bind=test_engine, autoflush=False, autocommit=False, future=True
    )
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)
