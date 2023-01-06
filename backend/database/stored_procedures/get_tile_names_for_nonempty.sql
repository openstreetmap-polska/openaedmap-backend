-- get all tile names (z, x, y) for tiles that have some data
CREATE OR REPLACE FUNCTION get_tile_names_for_non_empty() RETURNS TABLE (
    z smallint,
    x int,
    y int
)
AS  $$
    WITH
    tiles_with_nodes(z, x, y) as (
        SELECT DISTINCT z, lon2tile(ST_X(geometry), z) as x, lat2tile(ST_Y(geometry), z) as y
        FROM osm_nodes n
        CROSS JOIN generate_series(6, 13) as z
    ),
    low_zoom_tile_names(z, x, y, geometry) as (
        SELECT z, x, y, ST_Transform(ST_TileEnvelope(z, x, y), 4326) as geometry
        FROM get_tile_names(0, 5)
    ),
    tiles_with_countries(z, x, y) as (
        SELECT DISTINCT z, x, y
        FROM low_zoom_tile_names n
        JOIN countries c ON ST_Intersects(n.geometry, c.geometry)
    )
    SELECT z, x, y FROM tiles_with_countries
    UNION ALL
    SELECT z, x, y FROM tiles_with_nodes
    ;
$$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
