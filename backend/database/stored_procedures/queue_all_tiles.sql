CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles()
LANGUAGE SQL
SET work_mem TO '512MB'
BEGIN ATOMIC
    WITH
    tiles_to_refresh as (
        SELECT z, x, y, NTILE(1000) OVER(PARTITION BY z ORDER BY x, y) as bin
        FROM tiles
    )
    INSERT INTO tiles_queue
        SELECT z, x, y, (CURRENT_TIMESTAMP + (bin || ' minute')::interval) as ts
        FROM tiles_to_refresh
    ON CONFLICT DO NOTHING
    ;
END;
