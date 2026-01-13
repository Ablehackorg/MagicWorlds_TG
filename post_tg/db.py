from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

DB_ENGINE = os.getenv("DB_ENGINE", "postgresql")
DB_NAME   = os.getenv("DB_NAME", "bot_manager")
DB_USER   = os.getenv("DB_USER", "botuser")
DB_PASS   = os.getenv("DB_PASS", "botpass")
DB_HOST   = os.getenv("DB_HOST", "localhost")
DB_PORT   = os.getenv("DB_PORT", "5432")

if DB_ENGINE == "sqlite":
    DATABASE_URL = f"sqlite:///{os.getenv('SQLITE_PATH', '/db.sqlite3')}"
else:
    DATABASE_URL = f"{DB_ENGINE}+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(
                bind=engine,
                autoflush=False,
                autocommit=False,
                expire_on_commit=False
            )
Base = declarative_base()

