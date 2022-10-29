import json
import logging
import os
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.logger import logger as fastapi_logger
from sqlalchemy.orm import Session

from backend.api.v1.api import api_router
from backend.core.config import settings
from backend.country_parser import parse_countries
from backend.database.session import SessionLocal
from backend.models import Countries, OsmNodes
from backend.osm_loader import full_list_from_overpass, estimated_replication_sequence
from backend.schemas.countries import CountriesCreate

logger = logging.getLogger(__name__)
# https://github.com/tiangolo/uvicorn-gunicorn-fastapi-docker/issues/19
# without this block results from logger were not visible
if "gunicorn" in os.environ.get("SERVER_SOFTWARE", ""):
    gunicorn_error_logger = logging.getLogger("gunicorn.error")
    gunicorn_logger = logging.getLogger("gunicorn")

    fastapi_logger.setLevel(gunicorn_logger.level)
    fastapi_logger.handlers = gunicorn_error_logger.handlers
    logger.setLevel(gunicorn_logger.level)

    uvicorn_logger = logging.getLogger("uvicorn.access")
    uvicorn_logger.handlers = gunicorn_error_logger.handlers
else:
    # https://github.com/tiangolo/fastapi/issues/2019
    LOG_FORMAT2 = "[%(asctime)s %(process)d:%(threadName)s] %(name)s|%(filename)s:%(lineno)d - %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT2)

this_file_path = Path(__file__).absolute()

app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.on_event("startup")
async def startup_event():
    """Load countries and osm data if missing."""
    db: Session = SessionLocal()
    init_countries(db)
    load_osm_nodes_if_db_empty(db)


def init_countries(db: Session) -> None:
    if db.query(Countries).first() is None:
        logger.info("Loading countries to database...")
        counter = 0
        for c in parse_countries():
            country = Countries(**CountriesCreate(**c.__dict__).dict())  # todo: find a way to make this not terrible
            db.add(country)
            counter += 1
        db.commit()
        db.close()
        logger.info(f"Added {counter} rows.")
    else:
        logger.info("Countries already loaded.")


def make_sure_val_is_simple(value: int | float | str | dict) -> int | float | str:
    match value:
        case dict():
            return json.dumps(value)
        case _:
            return value


def load_osm_nodes_if_db_empty(db: Session) -> None:
    if db.query(OsmNodes).first() is None:
        logger.info("Full load of OSM Nodes from Overpass API...")
        replication_seq = estimated_replication_sequence(timedelta(minutes=-15))
        db.execute(statement="""
            BEGIN;
            CREATE TEMPORARY TABLE temp_nodes (
                node_id bigint,
                version int,
                uid int,
                "user" varchar,
                changeset int,
                latitude double precision,
                longitude double precision,
                tags jsonb
            );
        """)
        for n in full_list_from_overpass():
            params = {k: make_sure_val_is_simple(v) for k, v in n.__dict__.items()}
            db.execute(
                statement="""
                    INSERT INTO temp_nodes VALUES
                    (:node_id, :version, :uid, :user, :changeset, :latitude, :longitude, :tags);""",
                params=params
            )
        count = db.execute(statement="SELECT COUNT(*) FROM temp_nodes;").first()[0]
        logger.info(f"Inserted: {count} rows to temp table.")
        result = db.execute(statement="""
        WITH updated_country_codes as (
            INSERT INTO osm_nodes(node_id, version, creator_id, added_in_changeset, country_code, geometry, tags)
            SELECT
                node_id,
                version,
                CASE WHEN version = 1 THEN uid ELSE NULL END creator_id,
                CASE WHEN version = 1 THEN changeset ELSE NULL END added_in_changeset,
                country_code,
                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) as geometry,
                tags
            FROM temp_nodes n
            CROSS JOIN LATERAL (
                SELECT country_code
                FROM countries c
                WHERE ST_DWithin(c.geometry, ST_SetSRID(ST_MakePoint(longitude, latitude), 4326), 0.1)
                ORDER BY c.geometry <-> ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                LIMIT 1
            ) nearest_country
            RETURNING country_code
        ),
        counts as (
            SELECT
                country_code,
                count(*) as feature_count
            FROM updated_country_codes
            WHERE country_code IS NOT NULL
            GROUP BY country_code
        ),
        apply_updates as (
            UPDATE countries
            SET feature_count = counts.feature_count
            FROM counts
            WHERE countries.country_code = counts.country_code
            RETURNING 1
        ),
        insert_metadata as (
            INSERT INTO metadata (id, total_count, last_updated, last_processed_sequence)
            VALUES (0, (SELECT SUM(feature_count) FROM counts), :estimated_ts, :estimated_sequence)
            RETURNING 1
        )
        SELECT
            (SELECT COUNT(*) FROM updated_country_codes) as inserted_nodes,
            (SELECT COUNT(*) FROM apply_updates) as updated_countries,
            (SELECT COUNT(*) FROM insert_metadata) as inserted_metadata
        ;
        """, params={"estimated_sequence": replication_seq.formatted, "estimated_ts": replication_seq.timestamp})
        result = result.first()
        for k, v in zip(result.keys(), result):
            logger.info(f"Load result - {k}: {v}")
        db.execute(statement="COMMIT;")
    else:
        logger.info("Table with osm nodes is not empty. Skipping full load.")
