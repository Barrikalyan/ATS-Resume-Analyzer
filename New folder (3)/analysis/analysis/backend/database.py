"""
Database configuration and session management
"""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

try:
    from models import Base
except ModuleNotFoundError:  # support running as a package
    from .models import Base

import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./interview.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
if os.getenv("ENVIRONMENT", "development").lower() == "production" and not DATABASE_URL.startswith("postgresql+psycopg://"):
    raise RuntimeError("Set DATABASE_URL to Supabase PostgreSQL in production; local SQLite is disabled")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)
    if DATABASE_URL.startswith("postgresql+psycopg://"):
        with engine.begin() as connection:
            for table in Base.metadata.sorted_tables:
                connection.execute(text(f'ALTER TABLE public."{table.name}" ENABLE ROW LEVEL SECURITY'))

def get_db():
    """Dependency for getting database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
