CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles()
LANGUAGE SQL
SET work_mem TO '512MB'
BEGIN ATOMIC
    WITH
    tiles_to_refresh as (
        SELECT DISTINCT z, x, y
        FROM tiles t
        JOIN osm_nodes n ON ST_Intersects(ST_Transform(n.geometry, 3857), ST_TileEnvelope(t.z, t.x, t.y))
    ),
    tiles_to_refresh_with_offset as (
        SELECT z, x, y, NTILE(10) OVER(PARTITION BY z ORDER BY x, y) as bin
        FROM tiles_to_refresh
    )
    INSERT INTO tiles_queue
        SELECT z, x, y, (CURRENT_TIMESTAMP + (bin || ' minute')::interval) as ts
        FROM tiles_to_refresh_with_offset
    ON CONFLICT DO NOTHING
    ;
END;
