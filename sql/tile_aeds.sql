WITH settings AS (
    SELECT ST_TileEnvelope($1, $2, $3) AS tile, 20037508.342789244::float8 AS half_world
), locations AS (
    SELECT id AS node_id, NULL::bigint AS point_count, access, lon, lat
    FROM tile_locations($1, $2, $3) WHERE $1 = 16
    UNION ALL
    SELECT * FROM unnest($4::bigint[], $5::bigint[], $6::text[], $7::float8[], $8::float8[])
    WHERE $1 < 16
), projected AS (
    SELECT node_id, point_count, access,
           ST_Transform(ST_SetSRID(ST_MakePoint(lon, lat), 4326), 3857) AS point
    FROM locations
), encoded AS (
    SELECT node_id, point_count, abbreviate_count(point_count) AS point_count_abbreviated, access,
           ST_AsMVTGeom(ST_Translate(point,
               round(((ST_XMin(tile) + ST_XMax(tile)) / 2 - ST_X(point)) / (2 * half_world)) * 2 * half_world, 0),
               tile, 4096, 2048, false) AS geom
    -- Preserve BIRCH feature order: reordering changes frontend symbol rendering.
    FROM projected CROSS JOIN settings
)
SELECT ST_AsMVT(encoded.*, 'defibrillators', 4096, 'geom') FROM encoded
