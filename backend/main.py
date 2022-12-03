import logging
import os
import time
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.logger import logger as fastapi_logger
from fastapi.middleware.cors import CORSMiddleware
from fastapi_utils.tasks import repeat_every
from sqlalchemy.orm import Session

from backend.api.v1.api import api_router
from backend.core.config import settings
from backend.database.session import SessionLocal
from backend.init_functions import init_countries, load_osm_nodes_if_db_empty
from backend.models import Metadata
from backend.osm_loader import (
    changes_between_seq,
    find_newest_replication_sequence,
)


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
    allow_origins=allowed_origins,
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
    # load_osm_nodes_if_db_empty(db)
    db.close()
    # start functions that will run periodically
    # await load_changes()
    init_logger.info("Server finished startup procedure.")


@repeat_every(seconds=60, wait_first=True, logger=cron_logger)
def load_changes() -> None:
    cron_logger.info("Running update process...")
    process_start = time.perf_counter()
    db: Session = SessionLocal()
    db.execute(
        statement="""
        BEGIN;
        CREATE TEMPORARY TABLE temp_node_changes (
            change_type varchar,
            node_id bigint,
            version int,
            uid int,
            "user" varchar,
            changeset int,
            latitude double precision,
            longitude double precision,
            tags jsonb
        );
    """
    )
    current_meta: Metadata = db.query(Metadata).one()
    cron_logger.info(f"Current metadata: "
                f"last_processed_sequence={current_meta.last_processed_sequence}, "
                f"last_updated={current_meta.last_updated}")
    new_seq = find_newest_replication_sequence()
    for change in changes_between_seq(start_sequence=current_meta.last_processed_sequence, end_sequence=new_seq.number):
        db.execute(
            statement="""
                INSERT INTO temp_node_changes VALUES
                (:type, :node_id, :version, :uid, :user, :changeset, :latitude, :longitude, :tags);""",
            params=change.as_params_dict()
        )
    count = db.execute(statement="SELECT COUNT(*) FROM temp_node_changes;").first()[0]
    cron_logger.info(f"Inserted: {count} rows to temp table.")
    result = db.execute("""
    WITH
    changes as (
        SELECT *, ROW_NUMBER() OVER(PARTITION BY node_id ORDER BY version DESC) as rn
        FROM temp_node_changes
    ),
    last_changes as (
        SELECT *, c.tags @> '{"emergency":"defibrillator"}'::jsonb as is_defib
        FROM changes c
        WHERE rn=1
    ),
    nodes_with_removed_defibrillator_tag as (
        SELECT node_id
        FROM last_changes as c
        JOIN osm_nodes as n USING(node_id)
        WHERE 1=1
            AND c.version > n.version
            AND NOT c.is_defib
    ),
    deleted_nodes as (
        SELECT node_id, is_defib
        FROM last_changes as c
        WHERE change_type = 'delete'
    ),
    created_defibrillators as (
        SELECT
            node_id,
            version,
            uid,
            "user",
            changeset,
            latitude,
            longitude,
            tags,
            ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) as geometry,
            country_code
        FROM last_changes as c
        LEFT JOIN LATERAL (
            SELECT country_code
            FROM countries c
            WHERE ST_DWithin(c.geometry, ST_SetSRID(ST_MakePoint(longitude, latitude), 4326), 0.1)
            ORDER BY c.geometry <-> ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
            LIMIT 1
        ) nearest_country ON true
        WHERE 1=1
            AND change_type = 'create'
            AND is_defib
    ),
    modified_defibrillators as (
        SELECT
            node_id,
            version,
            uid,
            "user",
            changeset,
            latitude,
            longitude,
            tags,
            ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) as geometry,
            country_code
        FROM last_changes as c
        LEFT JOIN LATERAL (
            SELECT country_code
            FROM countries c
            WHERE ST_DWithin(c.geometry, ST_SetSRID(ST_MakePoint(longitude, latitude), 4326), 0.1)
            ORDER BY c.geometry <-> ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
            LIMIT 1
        ) nearest_country ON true
        WHERE 1=1
            AND change_type = 'modify'
            AND is_defib
    ),
    delete_defibrillators as (
        DELETE FROM osm_nodes as o
        USING nodes_with_removed_defibrillator_tag as rt, deleted_nodes as dn
        WHERE 1=1
            AND o.node_id = rt.node_id
            AND o.node_id = dn.node_id
        RETURNING o.node_id
    ),
    insert_defibrillators as (
        INSERT INTO osm_nodes (node_id, version, creator_id, added_in_changeset, country_code, geometry, tags)
        SELECT
            node_id,
            version,
            uid,
            changeset,
            country_code,
            geometry,
            tags
        FROM created_defibrillators
        ON CONFLICT DO NOTHING
        RETURNING node_id
    ),
    update_defibrillators as (
        UPDATE osm_nodes o
        SET (node_id, version, country_code, geometry, tags) = (m.node_id, m.version, m.country_code, m.geometry, m.tags)
        FROM modified_defibrillators m
        WHERE 1=1
            AND o.node_id = m.node_id
            AND m.version > o.version
        RETURNING o.node_id
    ),
    all_node_ids as (
        SELECT node_id FROM insert_defibrillators
        UNION
        SELECT node_id FROM update_defibrillators
        UNION
        SELECT node_id FROM delete_defibrillators
    ),
    country_codes_to_update as (
        SELECT DISTINCT country_code
        FROM osm_nodes
        JOIN all_node_ids USING(node_id)
    ),
    country_code_counts as (
        SELECT
            country_code,
            count(*) as feature_count
        FROM osm_nodes
        JOIN country_codes_to_update USING(country_code)
        GROUP BY country_code
    ),
    update_countries as (
        UPDATE countries
        SET feature_count = country_code_counts.feature_count
        FROM country_code_counts
        WHERE countries.country_code = country_code_counts.country_code
        RETURNING 1
    )
    SELECT
        (SELECT COUNT(*) FROM created_defibrillators) as osm_created_defibrillators,
        (SELECT COUNT(*) FROM modified_defibrillators) as osm_modified_defibrillators,
        (SELECT COUNT(*) FROM (
            SELECT node_id FROM nodes_with_removed_defibrillator_tag
            UNION
            SELECT node_id FROM deleted_nodes WHERE is_defib ) as dd
        ) as osm_deleted_defibrillators,
        (SELECT COUNT(*) FROM insert_defibrillators) as db_created_defibrillators,
        (SELECT COUNT(*) FROM update_defibrillators) as db_modified_defibrillators,
        (SELECT COUNT(*) FROM delete_defibrillators) as db_removed_defibrillators,
        (SELECT COUNT(*) FROM update_countries) as db_updated_countries
    """)
    for k, v in zip(result.keys(), result.first() or []):
        cron_logger.info(f"Load result - {k}: {v}")
    db.execute("""
        UPDATE metadata
        SET (total_count, last_updated, last_processed_sequence) = (
         (SELECT COUNT(*) FROM osm_nodes), :last_updated, :last_processed_sequence
        )
    """, params={"last_updated": new_seq.timestamp.isoformat(), "last_processed_sequence": new_seq.formatted})
    db.execute("DROP TABLE IF EXISTS temp_node_changes;")
    cron_logger.info("Updated data. Committing...")
    db.commit()
    cron_logger.info(f"Commit done. Data up to: {new_seq.formatted} - {new_seq.timestamp.isoformat()}")
    db.close()
    process_end = time.perf_counter()
    cron_logger.info(f"Update took: {round(process_end - process_start, 4)} seconds")
