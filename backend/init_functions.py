import logging
import time
from datetime import timedelta

from sqlalchemy.orm import Session

from backend.country_parser import parse_countries
from backend.models import Countries, OsmNodes, Tiles
from backend.osm_loader import (
    full_list_from_overpass,
    estimated_replication_sequence,
)
from backend.schemas.countries import CountriesCreate

logger = logging.getLogger(__name__)

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

def create_all_tiles(db: Session) -> None:
    min_zoom = 0
    max_zoom = 13
    query= """
    WITH
        zxy as (
            SELECT :zoom as z, x, y FROM generate_series(0, (2^:zoom-1)::int) as x, generate_series(0, (2^:zoom-1)::int) as y
        ),
        inserted as (
            INSERT INTO tiles
                SELECT z, x, y, mvt(z, x, y) FROM zxy
            ON CONFLICT (z, x, y) DO UPDATE SET mvt = excluded.mvt
            RETURNING z, x, y
        )
        SELECT count(*) FROM inserted
    """
    def run_level(zoom: int) -> None:
        logger.info(f"Creating all tiles for zoom: {zoom}")
        process_start = time.perf_counter()
        result = db.execute(query, params={"zoom": zoom}).first()
        num_tiles_processed = result[0] if result else 0
        db.commit()
        process_end = time.perf_counter()
        process_time = process_end - process_start  # in seconds
        tiles_per_s = num_tiles_processed / process_time
        logger.info(f"Creating tiles for zoom: {zoom} took: {round(process_time, 4)} seconds. "
                    f"{num_tiles_processed} tiles at {round(tiles_per_s, 1)} tiles/s")

    if db.query(Tiles).first() is None:
        logger.info("Table tiles empty.")
        for z in range(min_zoom, max_zoom + 1):
            run_level(z)
    else:
        logger.info("Table tiles contains rows. Skipping full reload of tiles.")
