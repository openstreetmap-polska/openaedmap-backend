import logging
import os
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.logger import logger as fastapi_logger
from fastapi.middleware.cors import CORSMiddleware
from fastapi_utils.tasks import repeat_every
from sqlalchemy.orm import Session

from backend.api.v1.api import api_router
from backend.core.config import settings
from backend.cron_functions import process_expired_tiles_queue, load_changes
from backend.database.session import SessionLocal
from backend.init_functions import init_countries, load_osm_nodes_if_db_empty, create_all_tiles

init_logger = logging.getLogger("init_logger")
cron_logger = logging.getLogger("cron_logger")
# https://github.com/tiangolo/uvicorn-gunicorn-fastapi-docker/issues/19
# without this block results from logger were not visible
if "gunicorn" in os.environ.get("SERVER_SOFTWARE", ""):
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    gunicorn_logger = logging.getLogger("gunicorn")

    fastapi_logger.setLevel(gunicorn_logger.level)
    fastapi_logger.handlers = gunicorn_error_logger.handlers
    cron_logger.setLevel(gunicorn_logger.level)
    init_logger.setLevel(gunicorn_logger.level)

    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.handlers = gunicorn_error_logger.handlers
else:
    # https://github.com/tiangolo/fastapi/issues/2019
    LOG_FORMAT2 = "[%(asctime)s %(process)d:%(threadName)s] %(name)s|%(filename)s:%(lineno)d - %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT2)

this_file_path = Path(__file__).absolute()

allowed_origins = [
    "https://openaedmap.org",
    "https://www.openaedmap.org",
    "https://dev.openaedmap.org",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app = FastAPI(
    title="Open AED Map - backend",
    description="API for [openaedmap.org](openaedmap.org)",
    license_info={
        "name": "ODbL",
        "url": "https://www.openstreetmap.org/copyright",
    },
    contact={
        "name": "OpenStreetMap Polska",
        "url": "https://github.com/openstreetmap-polska/openaedmap-backend",
    },
)
app.include_router(api_router, prefix=settings.API_V1_STR)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    max_age=int(timedelta(days=1).total_seconds()),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event():
    """Load data if missing."""
    init_logger.info("Running functions tied to server startup...")
    db: Session = SessionLocal()
    init_countries(db)
    load_osm_nodes_if_db_empty(db)
    create_all_tiles(db)
    db.close()
    # start functions that will run periodically
    await cron_load_changes()
    await cron_process_expired_tiles_queue()
    init_logger.info("Server finished startup procedure.")


@repeat_every(seconds=60, wait_first=True, logger=cron_logger)
def cron_load_changes() -> None:
    load_changes(cron_logger)


@repeat_every(seconds=60, wait_first=True, logger=cron_logger)
def cron_process_expired_tiles_queue() -> None:
    process_expired_tiles_queue(cron_logger)
