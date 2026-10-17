CREATE EXTENSION IF NOT EXISTS postgis;
ALTER EXTENSION postgis UPDATE;

CREATE TABLE IF NOT EXISTS aed (
    id bigint PRIMARY KEY,
    version bigint NOT NULL,
    tags jsonb,
    position geometry(Point, 4326),
    country_codes varchar(8)[]
);
-- Retain versions of former AEDs so delayed/replayed events cannot resurrect them.
ALTER TABLE aed ALTER COLUMN tags DROP NOT NULL;
ALTER TABLE aed ALTER COLUMN position DROP NOT NULL;
CREATE INDEX IF NOT EXISTS aed_position_idx ON aed USING gist (position);
CREATE INDEX IF NOT EXISTS aed_country_codes_idx ON aed USING gin (country_codes);

CREATE TABLE IF NOT EXISTS country (
    code varchar(8) PRIMARY KEY,
    names jsonb NOT NULL,
    geometry geometry(Geometry, 4326) NOT NULL,
    label_position geometry(Point, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS country_geometry_idx ON country USING gist (geometry);

CREATE TABLE IF NOT EXISTS state (
    key varchar PRIMARY KEY,
    data jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS map_revision (
    id boolean PRIMARY KEY DEFAULT true CHECK (id),
    revision bigint NOT NULL DEFAULT 0,
    country_revision bigint NOT NULL DEFAULT 0,
    feature_count bigint NOT NULL DEFAULT 0
);
INSERT INTO map_revision (id) VALUES (true) ON CONFLICT DO NOTHING;
CREATE TABLE IF NOT EXISTS country_count (
    code varchar(8) PRIMARY KEY,
    feature_count bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS country_tile (
    code varchar(8) NOT NULL REFERENCES country(code) ON DELETE CASCADE,
    zoom integer NOT NULL,
    geometry geometry(Geometry, 3857) NOT NULL,
    PRIMARY KEY (code, zoom)
);

CREATE TABLE IF NOT EXISTS photo (
    id varchar(32) PRIMARY KEY,
    node_id bigint NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
CREATE TABLE IF NOT EXISTS photo_report (
    id varchar(32) PRIMARY KEY,
    photo_id varchar(32) NOT NULL REFERENCES photo(id),
    created_at timestamptz NOT NULL DEFAULT statement_timestamp()
);
CREATE UNIQUE INDEX IF NOT EXISTS photo_report_photo_id_idx ON photo_report (photo_id);
CREATE INDEX IF NOT EXISTS photo_report_created_at_idx ON photo_report (created_at);

CREATE OR REPLACE FUNCTION refresh_map_counts() RETURNS void LANGUAGE sql AS $$
    DELETE FROM country_count;
    INSERT INTO country_count
    SELECT code, count(*) FROM aed
    CROSS JOIN LATERAL (SELECT DISTINCT unnest(country_codes) AS code) codes
    WHERE tags IS NOT NULL GROUP BY code;
    UPDATE map_revision SET feature_count = (SELECT count(*) FROM aed WHERE tags IS NOT NULL);
$$;

CREATE OR REPLACE FUNCTION assign_country_codes() RETURNS void LANGUAGE sql AS $$
    UPDATE aed SET country_codes = memberships.codes FROM (
        SELECT id, ARRAY(
            SELECT code FROM country WHERE ST_Intersects(country.geometry, a.position) ORDER BY code
        ) AS codes FROM aed a WHERE tags IS NOT NULL
    ) memberships
    WHERE aed.id = memberships.id AND aed.country_codes IS DISTINCT FROM memberships.codes;
$$;

CREATE OR REPLACE FUNCTION prepare_country_tiles() RETURNS void LANGUAGE sql AS $$
    DELETE FROM country_tile;
    INSERT INTO country_tile
    SELECT code, zoom, ST_Transform(ST_Intersection(
        ST_SimplifyPreserveTopology(geometry, 0.5 / power(2, zoom)),
        ST_MakeEnvelope(-180, -85.0511287798066, 180, 85.0511287798066, 4326)
    ), 3857)
    FROM country CROSS JOIN generate_series(3, 5) zoom;
$$;

CREATE OR REPLACE FUNCTION tile_locations(z integer, x integer, y integer)
RETURNS TABLE (id bigint, access text, lon float8, lat float8)
LANGUAGE sql STABLE PARALLEL SAFE AS $$
    WITH bounds AS (
        SELECT ST_Transform(ST_TileEnvelope(z, x, y), 4326) AS box
    ), buffered AS (
        SELECT ST_Expand(box, (ST_XMax(box) - ST_XMin(box)) / 2,
                              (ST_YMax(box) - ST_YMin(box)) / 2) AS box FROM bounds
    ), search AS (
        SELECT ST_Translate(box, shift_x, 0) AS box, shift_x
        FROM buffered CROSS JOIN (VALUES (-360), (0), (360)) shifts(shift_x)
        WHERE ST_XMin(box) + shift_x < 180 AND ST_XMax(box) + shift_x > -180
    )
    SELECT aed.id, coalesce(tags->>'access', ''), ST_X(position) - shift_x, ST_Y(position)
    FROM search JOIN aed ON ST_Intersects(position, box)
    WHERE tags IS NOT NULL;
$$;

CREATE OR REPLACE FUNCTION abbreviate_count(n bigint) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT AS $$
DECLARE
    suffix text;
    divisor double precision;
    bits bigint;
    exponent integer;
    numerator numeric;
    denominator numeric;
    tenths numeric;
    remainder numeric;
BEGIN
    IF n < 1000 THEN RETURN n::text; END IF;
    IF n >= 1000000 THEN suffix := 'm'; divisor := 1000000;
    ELSE suffix := 'k'; divisor := 1000; END IF;
    -- Match Python's .1f: round the binary64 quotient, ties to even.
    -- Casting it to numeric first loses the bits that distinguish e.g. 1.15.
    bits := ('x' || encode(float8send(n::double precision / divisor), 'hex'))::bit(64)::bigint;
    exponent := ((bits >> 52) & 2047)::integer - 1075;
    numerator := ((bits & 4503599627370495) + 4503599627370496)::numeric * 10;
    denominator := 1;
    IF exponent >= 0 THEN numerator := numerator * power(2::numeric, exponent);
    ELSE denominator := power(2::numeric, -exponent); END IF;
    tenths := div(numerator, denominator);
    remainder := mod(numerator, denominator);
    IF remainder * 2 > denominator OR (remainder * 2 = denominator AND mod(tenths, 2) = 1)
    THEN tenths := tenths + 1; END IF;
    RETURN div(tenths, 10)::text || '.' || mod(tenths, 10)::text || suffix;
END;
$$;

-- Initialize derived state when opening an existing pre-schema.sql database.
DO $$
BEGIN
    IF EXISTS (SELECT FROM aed WHERE tags IS NOT NULL AND country_codes IS NULL) THEN
        PERFORM assign_country_codes();
    END IF;
    IF NOT EXISTS (SELECT FROM state WHERE key = 'native_schema') THEN
        ALTER TABLE aed ADD CONSTRAINT aed_live_state CHECK ((tags IS NULL) = (position IS NULL));
        PERFORM refresh_map_counts();
        PERFORM prepare_country_tiles();
        INSERT INTO state VALUES ('native_schema', '{"version": 1}');
    END IF;
END;
$$;
