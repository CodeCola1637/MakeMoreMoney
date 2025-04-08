#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
from contextlib import contextmanager
import logging
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

# 获取数据库连接配置
DB_TYPE = os.environ.get("DB_TYPE", "sqlite")
DB_USER = os.environ.get("DB_USER", "")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_HOST = os.environ.get("DB_HOST", "")
DB_PORT = os.environ.get("DB_PORT", "")
DB_NAME = os.environ.get("DB_NAME", "trading_system")

# 构建连接URL
if DB_TYPE == "sqlite":
    DATABASE_URL = f"sqlite:///{os.path.join(os.getcwd(), 'databases', f'{DB_NAME}.db')}"
elif DB_TYPE == "mysql":
    DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
elif DB_TYPE == "postgresql":
    DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
else:
    raise ValueError(f"不支持的数据库类型: {DB_TYPE}")

# 创建日志记录器
logger = logging.getLogger("database")

# 创建引擎
if DB_TYPE == "sqlite":
    engine = create_engine(
        DATABASE_URL,
        echo=False,  # 设置为True以打印SQL语句
    )
else:
    engine = create_engine(
        DATABASE_URL,
        echo=False,  # 设置为True以打印SQL语句
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
    )

# 创建会话工厂
SessionFactory = sessionmaker(bind=engine)
SessionLocal = scoped_session(SessionFactory)

# 获取Base类
Base = declarative_base()

@contextmanager
def get_db_session():
    """
    创建一个数据库会话上下文管理器
    
    这个函数用于创建一个上下文管理器，可以在with语句中使用，
    并在退出时自动关闭会话。
    
    使用示例:
    ```
    with get_db_session() as session:
        user = session.query(User).filter(User.id == 1).first()
    ```
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"数据库操作出错: {e}")
        raise
    finally:
        session.close()

def init_db():
    """
    初始化数据库
    
    这个函数用于创建所有在模型中定义的表。
    如果表已经存在，则不会再次创建。
    """
    from databases.models import Base
    Base.metadata.create_all(bind=engine)
    logger.info("数据库表已创建")

def drop_db():
    """
    删除所有表
    
    这个函数用于删除所有在模型中定义的表。
    谨慎使用！
    """
    from databases.models import Base
    Base.metadata.drop_all(bind=engine)
    logger.info("数据库表已删除") 