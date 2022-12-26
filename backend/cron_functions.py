import datetime
import time
from logging import Logger

from backend import tiles_refresh_interval
from backend.database.session import SessionLocal
from backend.models import Metadata
from backend.osm_loader import find_newest_replication_sequence, changes_between_seq


def process_expired_tiles_queue(logger: Logger) -> None:
    min_zoom = 0
    max_zoom = 13
    query = """
        WITH
        tiles_to_update as (
            SELECT z, x, y
            FROM tiles_queue
            WHERE z = :zoom AND inserted_at < :older_than
            ORDER BY inserted_at DESC
            LIMIT 100000
            FOR UPDATE SKIP LOCKED
        ),
        updated_tiles as (
            UPDATE tiles t
            SET mvt = mvt(u.z, u.x, u.y)
            FROM tiles_to_update as u
            WHERE t.z=u.z AND t.x=u.x AND t.y=u.y
            RETURNING t.z, t.x, t.y
        ),
        deleted_queue_entries as (
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


# shouldn't be needed but prepared it just in case
# def queue_reload_of_all_tiles(logger: Logger) -> None:
#     logger.info("Queueing reload of all tiles...")
#     with SessionLocal() as db:
#         with db.begin():
#             db.execute("CALL queue_reload_of_all_tiles();")
#     logger.info("Finished putting all tiles into queue.")


def load_changes(logger: Logger) -> None:
    logger.info("Running update process...")
    process_start = time.perf_counter()
    with SessionLocal() as db:
        with db.begin():
            db.execute(statement="""
                CREATE TEMPORARY TABLE temp_node_changes (
                    change_type varchar,
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
            """)
            current_meta: Metadata = db.query(Metadata).one()
            logger.info(f"Current metadata: "
                        f"last_processed_sequence={current_meta.last_processed_sequence}, "
                        f"last_updated={current_meta.last_updated}")
            new_seq = find_newest_replication_sequence()
            for change in changes_between_seq(start_sequence=current_meta.last_processed_sequence, end_sequence=new_seq.number):
                db.execute(
                    statement="""
                        INSERT INTO temp_node_changes VALUES
                        (:type, :node_id, :version, :uid, :user, :changeset, :latitude, :longitude, :tags, :version_timestamp);""",
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
            delete_defibrillators as (
                DELETE FROM osm_nodes as o
                USING (
                    SELECT node_id FROM deleted_nodes
                    UNION
                    SELECT node_id FROM nodes_with_removed_defibrillator_tag
                ) d
                WHERE o.node_id = d.node_id
                RETURNING o.node_id
            ),
            insert_defibrillators as (
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
                RETURNING node_id
            ),
            update_defibrillators as (
                UPDATE osm_nodes o
                SET (node_id, version, country_code, geometry, tags, version_last_ts) = (m.node_id, m.version, m.country_code, m.geometry, m.tags, m.version_timestamp)
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
            ),
            insert_expired_tiles_to_queue as (
                INSERT INTO tiles_queue(z, x, y, inserted_at)
                    SELECT DISTINCT z, x, y, CURRENT_TIMESTAMP
                    FROM tiles
                    JOIN (
                        SELECT ST_Transform(geometry, 3857) geometry
                        FROM osm_nodes
                        JOIN all_node_ids USING (node_id)
                    ) as nodes ON ST_Intersects(nodes.geometry, ST_TileEnvelope(tiles.z, tiles.x, tiles.y))
                ON CONFLICT DO NOTHING
                RETURNING z, x, y
            )
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
            db.execute("DROP TABLE IF EXISTS temp_node_changes;")
            logger.info("Updated data. Committing...")
    logger.info(f"Commit done. Data up to: {new_seq.formatted} - {new_seq.timestamp.isoformat()}")
    process_end = time.perf_counter()
    logger.info(f"Update took: {round(process_end - process_start, 4)} seconds")
