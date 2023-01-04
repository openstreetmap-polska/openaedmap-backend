-- get all tile names (x, y) for given zoom level
CREATE OR REPLACE FUNCTION get_tile_names (IN zoom smallint) RETURNS TABLE (
    z smallint,
    x int,
    y int
)
AS  $$
    SELECT zoom as z, x, y FROM generate_series(0, (2^zoom-1)::int) as x, generate_series(0, (2^zoom-1)::int) as y;
-- version written in Standard SQL, above takes around 7 seconds for zoom 13, below takes 8 s
--    WITH RECURSIVE
--    xes as (
--        SELECT 0 as x
--        UNION ALL
--        SELECT x+1 FROM xes WHERE x < 2^zoom-1
--    ),
--    yes as (
--        SELECT 0 as y
--        UNION ALL
--        SELECT y+1 FROM yes WHERE y < 2^zoom-1
--    )
--    SELECT zoom as z, x, y FROM xes, yes;
$$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
