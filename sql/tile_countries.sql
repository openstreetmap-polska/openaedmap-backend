WITH bounds AS (SELECT ST_TileEnvelope($1, $2, $3) AS tile),
selected AS MATERIALIZED (
    SELECT code, names, label_position, coalesce(feature_count, 0) AS feature_count
    FROM country LEFT JOIN country_count USING (code), bounds
    WHERE ST_Intersects(geometry, ST_Transform(tile, 4326))
), outlines AS (
    SELECT ST_AsMVTGeom(country_tile.geometry, tile, 4096, 64, true) AS geom
    FROM selected JOIN country_tile USING (code), bounds WHERE zoom = $1 ORDER BY code
), labels AS (
    SELECT names->>'default' AS country_name, code AS country_code,
           feature_count AS point_count, abbreviate_count(feature_count) AS point_count_abbreviated,
           ST_AsMVTGeom(ST_Transform(label_position, 3857), tile, 4096, 64, false) AS geom
    FROM selected, bounds ORDER BY code
)
SELECT (SELECT ST_AsMVT(outlines.*, 'countries', 4096, 'geom') FROM outlines) AS countries,
       (SELECT ST_AsMVT(labels.*, 'defibrillators', 4096, 'geom') FROM labels) AS labels
