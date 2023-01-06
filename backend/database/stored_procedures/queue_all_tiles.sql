CREATE OR REPLACE PROCEDURE queue_reload_of_all_tiles()
LANGUAGE SQL
BEGIN ATOMIC
    INSERT INTO tiles_queue(z, x, y, inserted_at)
        SELECT z, x, y, CURRENT_TIMESTAMP - '1 day'::interval as ts
        FROM get_tile_names_for_non_empty()
    ON CONFLICT DO NOTHING
    ;
END;
