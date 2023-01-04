-- functions from https://wiki.openstreetmap.org/wiki/Slippy_map_tilenames#PostgreSQL


-- longitude to x
CREATE OR REPLACE FUNCTION lon2tile(lon DOUBLE PRECISION, zoom INTEGER) RETURNS INTEGER AS $$
    SELECT FLOOR( (lon + 180) / 360 * (1 << zoom) )::INTEGER;
$$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE;


-- latitude to y
CREATE OR REPLACE FUNCTION lat2tile(lat double precision, zoom integer) RETURNS integer AS $$
    SELECT floor( (1.0 - ln(tan(radians(lat)) + 1.0 / cos(radians(lat))) / pi()) / 2.0 * (1 << zoom) )::integer;
$$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE;


CREATE OR REPLACE FUNCTION tile2lat(y integer, zoom integer) RETURNS double precision AS $$
DECLARE
    n float;
    sinh float;
    E float = 2.7182818284;
BEGIN
    -- This code returns the coordinate of the _upper left_ (northwest-most)-point of the tile.
    n = pi() - (2.0 * pi() * y) / power(2.0, zoom);
    sinh = (1 - power(E, -2*n)) / (2 * power(E, -n));
    return degrees(atan(sinh));
END;
$$ LANGUAGE plpgsql IMMUTABLE PARALLEL SAFE;


CREATE OR REPLACE FUNCTION tile2lon(x integer, zoom integer) RETURNS double precision AS $$
    -- This code returns the coordinate of the _upper left_ (northwest-most)-point of the tile.
    SELECT CAST(x * 1.0 / (1 << zoom) * 360.0 - 180.0 AS double precision);
$$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE;
