CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles(min_zoom int, max_zoom int)
LANGUAGE SQL
SET work_mem TO '512MB'
BEGIN ATOMIC
    WITH
    tiles_to_refresh as (
        SELECT DISTINCT z, lon2tile(ST_X(geometry), z) as x, lat2tile(ST_Y(geometry), z) as y
        FROM osm_nodes n
        CROSS JOIN generate_series(min_zoom, max_zoom) as z
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
