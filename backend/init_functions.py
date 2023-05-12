import logging
import os
import time
from datetime import timedelta

from geoalchemy2 import func
from sqlalchemy import nullslast

from backend.country_parser import parse_countries
from backend.data_file_generator import ExportedNode, save_geojson_file
from backend.database.session import SessionLocal
from backend.models import Countries, OsmNodes, Tiles
from backend.osm_loader import full_list_from_overpass, estimated_replication_sequence
from backend.schemas.countries import CountriesCreate

logger = logging.getLogger(__name__)


def init_countries() -> None:
    with SessionLocal() as db:
        if db.query(Countries).first() is None:
            db.commit()  # close previous transaction
            logger.info("Loading countries to database...")
            counter = 0
            process_start = time.perf_counter()
            with db.begin():
                for c in parse_countries():
                    country = Countries(
                        **CountriesCreate(**c.__dict__).dict()
                    )  # todo: find a way to make this not terrible
                    db.add(country)
                    counter += 1
            process_end = time.perf_counter()
            logger.info(f"Added {counter} rows.")
            logger.info(f"Load took: {round(process_end - process_start, 4)} seconds")
        else:
            logger.info("Countries already loaded.")


def load_osm_nodes_if_db_empty() -> None:
    with SessionLocal() as db:
        if db.query(OsmNodes).first() is None:
            db.commit()  # close previous transaction
            logger.info("Full load of OSM Nodes from Overpass API...")
            process_start = time.perf_counter()
            replication_seq = estimated_replication_sequence(timedelta(minutes=-15))
            with db.begin():
                db.execute(
                    statement="""
                    CREATE TEMPORARY TABLE temp_nodes (
                        node_id bigint,
                        version int,
                        uid int,
                        "user" varchar,
                        changeset int,
                        latitude double precision,
                        longitude double precision,
                        tags jsonb,
                        version_timestamp timestamp with time zone
                    );
                """
                )
                for n in full_list_from_overpass():
                    db.execute(
                        statement="""
                            INSERT INTO temp_nodes VALUES
                            (:node_id, :version, :uid, :user, :changeset, :latitude, :longitude, :tags, :version_timestamp);""",
                        params=n.as_params_dict(),
                    )
                count = db.execute(
                    statement="SELECT COUNT(*) FROM temp_nodes;"
                ).first()[0]
                logger.info(f"Inserted: {count} rows to temp table.")
                result = db.execute(
                    statement="""
                WITH
                updated_country_codes as (
                    INSERT INTO osm_nodes(node_id, version, creator_id, added_in_changeset, country_code, geometry, tags, version_1_ts, version_last_ts)
                    SELECT
                        node_id,
                        version,
                        CASE WHEN version = 1 THEN uid ELSE NULL END creator_id,
                        CASE WHEN version = 1 THEN changeset ELSE NULL END added_in_changeset,
                        country_code,
                        ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) as geometry,
                        tags,
                        CASE WHEN version = 1 THEN version_timestamp ELSE NULL END version_1_ts,
                        version_timestamp as version_last_ts
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
                    (SELECT COUNT(*) FROM counts) as countries_to_update,
                    (SELECT COUNT(*) FROM apply_updates) as updated_countries,
                    (SELECT COUNT(*) FROM insert_metadata) as inserted_metadata
                ;
                """,
                    params={
                        "estimated_sequence": replication_seq.formatted,
                        "estimated_ts": replication_seq.timestamp,
                    },
                )
                result = result.first()
                for k, v in zip(result.keys(), result):
                    logger.info(f"Load result - {k}: {v}")
                db.execute("DROP TABLE IF EXISTS temp_nodes;")
            process_end = time.perf_counter()
            logger.info(f"Load took: {round(process_end - process_start, 4)} seconds")
        else:
            logger.info("Table with osm nodes is not empty. Skipping full load.")


def create_all_tiles() -> None:
    min_zoom = 0
    max_zoom = 13
    query = """
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
        with db.begin():
            result = db.execute(query, params={"zoom": zoom}).first()
        num_tiles_processed = result[0] if result else 0
        process_end = time.perf_counter()
        process_time = process_end - process_start  # in seconds
        tiles_per_s = num_tiles_processed / process_time
        logger.info(
            f"Creating tiles for zoom: {zoom} took: {round(process_time, 4)} seconds. "
            f"{num_tiles_processed} tiles at {round(tiles_per_s, 1)} tiles/s"
        )

    with SessionLocal() as db:
        if db.query(Tiles).first() is None:
            db.commit()  # close previous transaction
            logger.info("Table tiles empty.")
            for z in range(min_zoom, max_zoom + 1):
                run_level(z)
        else:
            logger.info("Table tiles contains rows. Skipping full reload of tiles.")


def generate_data_files_for_all_countries() -> None:
    if len(os.listdir("/data")) > 0:
        logger.info(
            "There are files present in /data dir. Skipping generating all data files."
        )
    else:
        logger.info("Generating data files...")
        logger.info("Getting data out of db.")
        process_start = time.perf_counter()
        with SessionLocal() as db:
            country_codes = set([c[0] for c in db.query(Countries.country_code)])
            nodes = (
                db.query(
                    OsmNodes.node_id,
                    OsmNodes.country_code,
                    OsmNodes.tags,
                    func.ST_X(OsmNodes.geometry),
                    func.ST_Y(OsmNodes.geometry),
                )
                .order_by(nullslast(OsmNodes.country_code.asc()))
                .all()
            )
        world_data = [ExportedNode(*n) for n in nodes]
        process_end = time.perf_counter()
        process_time = process_end - process_start  # in seconds
        logger.info(
            f"Finished getting data out of db. It took: {round(process_time, 4)} seconds"
        )
        save_geojson_file(f"/data/world.geojson", world_data)
        buffer: list[ExportedNode] = []
        current_country = world_data[0].country_code
        for node in world_data:
            if node.country_code is not None:
                if node.country_code != current_country:
                    save_geojson_file(f"/data/{current_country}.geojson", buffer)
                    country_codes.remove(current_country)
                    current_country = node.country_code
                    buffer = []
                else:
                    buffer.append(node)
        if len(buffer) > 0:
            save_geojson_file(f"/data/{current_country}.geojson", buffer)
            country_codes.remove(current_country)
        for country_code in country_codes:
            save_geojson_file(f"/data/{country_code}.geojson", data=None)
        logger.info("Finished generating data files.")
