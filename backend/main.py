import json
import logging
import os
import time
from datetime import timedelta
from pathlib import Path

from fastapi_utils.tasks import repeat_every
from fastapi import FastAPI
from fastapi.logger import logger as fastapi_logger
from sqlalchemy.orm import Session

from backend.api.v1.api import api_router
from backend.core.config import settings
from backend.country_parser import parse_countries
from backend.database.session import SessionLocal
from backend.models import Countries, OsmNodes, Metadata
from backend.osm_loader import (
    full_list_from_overpass, estimated_replication_sequence, changes_between_seq,
    find_newest_replication_sequence,
)
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


@app.on_event("startup")
async def startup_event():
    """Load countries and osm data if missing."""
    db: Session = SessionLocal()
    init_countries(db)
    load_osm_nodes_if_db_empty(db)
    db.close()
    await load_changes()


def init_countries(db: Session) -> None:
    if db.query(Countries).first() is None:
        logger.info("Loading countries to database...")
        counter = 0
        process_start = time.perf_counter()
        for c in parse_countries():
            country = Countries(**CountriesCreate(**c.__dict__).dict())  # todo: find a way to make this not terrible
            db.add(country)
            counter += 1
        db.commit()
        process_end = time.perf_counter()
        logger.info(f"Added {counter} rows.")
        logger.info(f"Load took: {round(process_end - process_start, 4)} seconds")
    else:
        logger.info("Countries already loaded.")


def load_osm_nodes_if_db_empty(db: Session) -> None:
    if db.query(OsmNodes).first() is None:
        logger.info("Full load of OSM Nodes from Overpass API...")
        process_start = time.perf_counter()
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
            db.execute(
                statement="""
                    INSERT INTO temp_nodes VALUES
                    (:node_id, :version, :uid, :user, :changeset, :latitude, :longitude, :tags);""",
                params=n.as_params_dict()
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
            LEFT JOIN LATERAL (
                SELECT country_code
                FROM countries c
                WHERE ST_DWithin(c.geometry, ST_SetSRID(ST_MakePoint(longitude, latitude), 4326), 0.1)
                ORDER BY c.geometry <-> ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)
                LIMIT 1
            ) nearest_country ON true
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
        db.execute("DROP TABLE IF EXISTS temp_nodes;")
        db.commit()
        process_end = time.perf_counter()
        logger.info(f"Load took: {round(process_end - process_start, 4)} seconds")
    else:
        logger.info("Table with osm nodes is not empty. Skipping full load.")


@repeat_every(seconds=60, wait_first=True, logger=logger)
def load_changes() -> None:
    logger.info("Running update process...")
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
    logger.info(f"Current metadata: "
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
    logger.info(f"Inserted: {count} rows to temp table.")
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
        logger.info(f"Load result - {k}: {v}")
    db.execute("""
        UPDATE metadata
        SET (total_count, last_updated, last_processed_sequence) = (
         (SELECT COUNT(*) FROM osm_nodes), :last_updated, :last_processed_sequence
        )
    """, params={"last_updated": new_seq.timestamp.isoformat(), "last_processed_sequence": new_seq.formatted})
    db.execute("DROP TABLE IF EXISTS temp_node_changes;")
    logger.info("Updated data. Committing...")
    db.commit()
    logger.info(f"Commit done. Data up to: {new_seq.formatted} - {new_seq.timestamp.isoformat()}")
    db.close()
    process_end = time.perf_counter()
    logger.info(f"Update took: {round(process_end - process_start, 4)} seconds")
