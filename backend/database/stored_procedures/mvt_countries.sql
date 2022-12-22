CREATE OR REPLACE FUNCTION mvt_countries (IN bbox geometry) RETURNS bytea
AS  $$

    WITH
        points as (
            SELECT
                ST_AsMVTGeom(ST_Transform(label_point, 3857), bbox),
                country_code,
                country_names ->> 'default' as country_name,
                feature_count as point_count,
                abbreviated(feature_count) as point_count_abbreviated
            FROM countries
            WHERE ST_Intersects(ST_Transform(label_point, 3857), bbox)
            AND NOT (
                label_point && ST_MakeEnvelope(-180, -90, 180, -85, 4326)
                OR
                label_point && ST_MakeEnvelope(-180,  85, 180,  90, 4326)
            )
        ),
        areas as (
            SELECT
                ST_AsMVTGeom(ST_SimplifyPreserveTopology(ST_Transform(ST_Intersection(geometry, ST_MakeEnvelope(-180, -85, 180, 85, 4326)), 3857), 5000), bbox),
                country_code,
                country_names ->> 'default' as country_name,
                feature_count as point_count,
                abbreviated(feature_count) as point_count_abbreviated
            FROM countries
            WHERE ST_Intersects(geometry, ST_Transform(bbox, 4326))
        ),
        layers as (
            SELECT ST_AsMVT(points.*, 'defibrillators') as data
            FROM points
            UNION ALL
            SELECT ST_AsMVT(areas.*, 'countries')
            FROM areas
        )
        SELECT string_agg(data, null)
        FROM layers

$$ LANGUAGE SQL STABLE ;
