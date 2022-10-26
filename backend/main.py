import logging
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy.orm import Session
from backend.database.session import SessionLocal

from backend.api.v1.api import api_router
from backend.core.config import settings
from backend.country_parser import parse_countries
from backend.models import Countries
from backend.schemas.countries import CountriesCreate


this_file_path = Path(__file__).absolute()

logger = logging.getLogger(__name__)
app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.on_event("startup")
async def startup_event():
    """Load countries and osm data if missing."""
    db: Session = SessionLocal()
    if db.query(Countries).first() is None:
        logger.info("Loading countries to database...")
        counter = 0
        for c in parse_countries():
            country = Countries(**CountriesCreate(**c.__dict__).dict())
            db.add(country)
            counter += 1
        db.commit()
        db.close()
        logger.info(f"Added {counter} rows.")
    else:
        logger.info("Countries already loaded.")
