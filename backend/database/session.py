from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.config import settings

engine = create_engine(
    settings.DATABASE_URL, pool_size=20, max_overflow=50, pool_recycle=3600
)
SessionLocal = sessionmaker(bind=engine)
