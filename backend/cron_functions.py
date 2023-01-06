import datetime
import time
from logging import Logger

import sqlalchemy
from geoalchemy2 import func
from sqlalchemy import nullslast, insert

from backend import tiles_refresh_interval, min_zoom, max_zoom
from backend.data_file_generator import ExportedNode, save_geojson_file
from backend.database.session import SessionLocal
from backend.models import Metadata, OsmNodes
from backend.models.temp_node_changes import TempNodeChanges
from backend.osm_loader import find_newest_replication_sequence, changes_between_seq


def process_expired_tiles_queue(logger: Logger) -> None:
    query = """
        WITH
        tiles_to_update(z, x, y) as (
            SELECT z, x, y
            FROM tiles_queue
            WHERE z = :zoom AND inserted_at < :older_than
            LIMIT 100000
            FOR UPDATE SKIP LOCKED
        ),
        updated_tiles(z, x, y) as (
            INSERT INTO tiles
                SELECT z, x, y, mvt(z, x, y) FROM tiles_to_update
            ON CONFLICT (z, x, y) DO UPDATE SET mvt = excluded.mvt
            RETURNING z, x, y
        ),
        deleted_queue_entries(z, x, y) as (
            DELETE FROM tiles_queue q
            USING updated_tiles u
            WHERE q.z=u.z AND q.x=u.x AND q.y=u.y
            RETURNING q.z, q.x, q.y
        )
        SELECT
            (SELECT COUNT(*) FROM updated_tiles) as number_of_updated_tiles,
            (SELECT COUNT(*) FROM deleted_queue_entries) as deleted_queue_entries
    """
    def run_level(zoom: int, older_than: datetime.datetime) -> None:
        with db.begin():
            result = db.execute(query, params={"zoom": zoom, "older_than": older_than})
            result = result.first()
            for k, v in zip(result.keys(), result):
                logger.info(f"Results (zoom: {zoom}) - {k}: {v}")

    logger.info("Processing expired tiles queue...")
    process_start = time.perf_counter()
    with SessionLocal() as db:
        for z in range(min_zoom, max_zoom + 1):
            ts = datetime.datetime.utcnow() - tiles_refresh_interval[z]
            run_level(zoom=z, older_than=ts)
    process_end = time.perf_counter()
    logger.info(f"Processing expired tiles queue took: {round(process_end - process_start, 4)} seconds")


def queue_reload_of_all_tiles(logger: Logger) -> None:
    logger.info("Queueing reload of all tiles...")
    with SessionLocal() as db:
        with db.begin():
            db.execute("CALL queue_reload_of_all_tiles()")
    logger.info("Finished putting all tiles into queue.")


def load_changes(logger: Logger) -> None:
    logger.info("Running update process...")
    process_start = time.perf_counter()
    with SessionLocal() as db:
        with db.begin():
            current_meta: Metadata = db.query(Metadata).one()
            logger.info(f"Current metadata: "
                        f"last_processed_sequence={current_meta.last_processed_sequence}, "
                        f"last_updated={current_meta.last_updated}")
            new_seq = find_newest_replication_sequence()
            db.execute(
                insert(TempNodeChanges.__table__),
                [
                    change.as_params_dict()
                    for change in changes_between_seq(
                        start_sequence=current_meta.last_processed_sequence, end_sequence=new_seq.number, skip_first=True
                    )
                ]
            )
            logger.info(f"Inserted: {db.query(TempNodeChanges).count()} rows to temp table.")
            result = db.execute("""
            WITH
            -- raw data
            changes(
                change_type, node_id, version, uid, "user", changeset, latitude, longitude, tags, version_timestamp, rn
            ) as (
                SELECT *, ROW_NUMBER() OVER(PARTITION BY node_id ORDER BY version DESC) as rn
                FROM temp_node_changes
            ),
            last_changes(
                change_type, node_id, version, uid, "user", changeset, latitude, longitude, tags, version_timestamp, rn, is_defib
            ) as (
                SELECT *, c.tags @> '{"emergency":"defibrillator"}'::jsonb as is_defib
                FROM changes c
                WHERE rn=1
            ),
            -- classified data
            nodes_with_removed_defibrillator_tag(node_id) as (
                SELECT node_id
                FROM last_changes as c
                JOIN osm_nodes as n USING(node_id)
                WHERE 1=1
                    AND c.version > n.version
                    AND NOT c.is_defib
            ),
            deleted_nodes(node_id, is_defib) as (
                SELECT node_id, is_defib
                FROM last_changes as c
                WHERE change_type = 'delete'
            ),
            created_defibrillators(
                node_id, version, uid, "user", changeset, latitude, longitude, tags, geometry, country_code, version_timestamp
            ) as (
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
                    country_code,
                    version_timestamp
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
            modified_defibrillators(
                node_id, version, uid, "user", changeset, latitude, longitude, tags, geometry, country_code, version_timestamp
            ) as (
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
                    country_code,
                    version_timestamp
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
            -- apply updates
            delete_defibrillators(node_id, country_code, geometry) as (
                DELETE FROM osm_nodes as o
                USING (
                    SELECT node_id FROM deleted_nodes
                    UNION
                    SELECT node_id FROM nodes_with_removed_defibrillator_tag
                ) d
                WHERE o.node_id = d.node_id
                RETURNING o.node_id, o.country_code, o.geometry
            ),
            insert_defibrillators(node_id, country_code, geometry) as (
                INSERT INTO osm_nodes (node_id, version, creator_id, added_in_changeset, country_code, geometry, tags, version_1_ts, version_last_ts)
                    SELECT
                        node_id,
                        version,
                        uid,
                        changeset,
                        country_code,
                        geometry,
                        tags,
                        version_timestamp,
                        version_timestamp
                    FROM created_defibrillators
                ON CONFLICT DO NOTHING
                RETURNING node_id, country_code, geometry
            ),
            update_defibrillators(node_id, country_code, geometry) as (
                UPDATE osm_nodes o
                SET (node_id, version, country_code, geometry, tags, version_last_ts) = (m.node_id, m.version, m.country_code, m.geometry, m.tags, m.version_timestamp)
                FROM modified_defibrillators m
                WHERE 1=1
                    AND o.node_id = m.node_id
                    AND m.version > o.version
                RETURNING m.node_id, m.country_code, m.geometry
            ),
            -- after update actions
            all_node_ids(node_id) as (
                SELECT node_id FROM insert_defibrillators
                UNION
                SELECT node_id FROM update_defibrillators
                UNION
                SELECT node_id FROM delete_defibrillators
            ),
            country_codes_to_update(country_code) as (
                SELECT country_code
                FROM (
                    SELECT DISTINCT country_code FROM insert_defibrillators
                    UNION
                    SELECT DISTINCT country_code FROM update_defibrillators
                    UNION
                    SELECT DISTINCT country_code FROM delete_defibrillators
                ) c
                WHERE country_code IS NOT NULL
            ),
            country_code_counts(country_code, feature_count) as (
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
            ),
            insert_country_codes_to_queue(country_code) as (
                INSERT INTO countries_queue(country_code)
                    SELECT country_code
                    FROM country_codes_to_update
                RETURNING country_code
            ),
            low_zoom_tile_names(z, x, y) as (
                SELECT DISTINCT z, x, y
                FROM (
                    SELECT z, x, y, ST_Transform(ST_TileEnvelope(z, x, y), 4326) as geometry
                    FROM get_tile_names(0, 5)
                ) n
                JOIN countries c ON ST_Intersects(n.geometry, c.geometry)
            ),
            tiles_with_nodes(z, x, y) as (
                SELECT DISTINCT z, lon2tile(ST_X(geometry), z) as x, lat2tile(ST_Y(geometry), z) as y
                FROM (
                    SELECT geometry FROM insert_defibrillators
                    UNION
                    SELECT geometry FROM update_defibrillators
                    UNION
                    SELECT geometry FROM delete_defibrillators
                ) n
                CROSS JOIN generate_series(6, 13) as z
            ),
            tiles_to_update(z, x, y) as (
                SELECT z, x, y FROM low_zoom_tile_names
                UNION ALL
                SELECT z, x, y FROM tiles_with_nodes
            ),
            insert_expired_tiles_to_queue(z, x, y) as (
                INSERT INTO tiles_queue(z, x, y, inserted_at)
                    SELECT z, x, y, CURRENT_TIMESTAMP
                    FROM tiles_to_update
                ON CONFLICT DO NOTHING
                RETURNING z, x, y
            )
            -- summary
            SELECT
                (SELECT COUNT(*) FROM changes) as inserted_node_changes,
                (SELECT COUNT(*) FROM last_changes) as newest_changes_per_object,
                (SELECT COUNT(*) FROM nodes_with_removed_defibrillator_tag) as osm_nodes_with_removed_defibrillator_tag,
                (SELECT COUNT(*) FROM deleted_nodes) as osm_deleted_nodes,
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
                (SELECT COUNT(*) FROM all_node_ids) as db_all_node_ids,
                (SELECT COUNT(*) FROM country_codes_to_update) as db_country_codes_to_update,
                (SELECT COUNT(*) FROM country_code_counts) as db_country_code_counts,
                (SELECT COUNT(*) FROM update_countries) as db_updated_countries,
                (SELECT COUNT(*) FROM insert_country_codes_to_queue) as inserted_country_codes_to_queue_to_gen_files,
                (SELECT COUNT(*) FROM insert_expired_tiles_to_queue) as expired_tiles_added_to_queue
            """)
            for k, v in zip(result.keys(), result.first() or []):
                logger.info(f"Load result - {k}: {v}")
            db.execute("""
                UPDATE metadata
                SET (total_count, last_updated, last_processed_sequence) = (
                    (SELECT COUNT(*) FROM osm_nodes), :last_updated, :last_processed_sequence
                )
            """, params={"last_updated": new_seq.timestamp.isoformat(), "last_processed_sequence": new_seq.formatted})
            db.execute("DELETE FROM temp_node_changes")
            logger.info("Updated data. Committing...")
    logger.info(f"Commit done. Data up to: {new_seq.formatted} - {new_seq.timestamp.isoformat()}")
    process_end = time.perf_counter()
    logger.info(f"Update took: {round(process_end - process_start, 4)} seconds")


def generate_data_files_for_countries_with_data(logger: Logger) -> None:
    with SessionLocal() as db:
        codes = [x[0] for x in db.execute("SELECT country_code FROM countries_queue FOR UPDATE SKIP LOCKED").all()]
        if len(codes) > 0:
            db.commit()  # close previous transaction
            with db.begin():
                logger.info("Generating data files...")
                logger.info("Getting data out of db.")
                process_start = time.perf_counter()
                nodes = db.query(
                    OsmNodes.node_id,
                    OsmNodes.country_code,
                    OsmNodes.tags,
                    func.ST_X(OsmNodes.geometry),
                    func.ST_Y(OsmNodes.geometry),
                ).order_by(nullslast(OsmNodes.country_code.asc())).all()
                world_data = [ExportedNode(*n) for n in nodes]
                process_end = time.perf_counter()
                process_time = process_end - process_start  # in seconds
                logger.info(f"Finished getting data out of db. It took: {round(process_time, 4)} seconds")
                save_geojson_file(f"/data/world.geojson", world_data)
                buffer: list[ExportedNode] = []
                distinct_codes = set(codes)
                starting_index = 0
                while world_data[starting_index].country_code not in distinct_codes and starting_index < len(world_data) - 1:
                    starting_index += 1
                current_country = world_data[starting_index].country_code
                for node in world_data[starting_index:]:
                    if node.country_code is not None and node.country_code in distinct_codes:
                        if node.country_code != current_country:
                            save_geojson_file(f"/data/{current_country}.geojson", buffer)
                            current_country = node.country_code
                            buffer = []
                        else:
                            buffer.append(node)
                if len(buffer) > 0:
                    save_geojson_file(f"/data/{current_country}.geojson", buffer)
                db.execute(sqlalchemy.text("DELETE FROM countries_queue WHERE country_code = ANY(:codes)"), params={"codes": list(distinct_codes)})
            logger.info("Finished generating data files.")
        else:
            logger.info("No (unlocked) rows in countries_queue. Skipping writing files.")
