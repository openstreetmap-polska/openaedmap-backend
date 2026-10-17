WITH batch AS MATERIALIZED (
    SELECT id, version, tags, position FROM aed
    WHERE id > $1 AND tags IS NOT NULL
      /* COUNTRY_FILTER */
    ORDER BY id LIMIT 1000
)
SELECT max(id) AS next_id, count(*) AS count,
       convert_to(string_agg(jsonb_build_object(
           'type', 'Feature',
           'geometry', jsonb_build_object('type', 'Point',
               'coordinates', jsonb_build_array(ST_X(position), ST_Y(position))),
           'properties', jsonb_build_object(
               '@osm_type', 'node', '@osm_id', id, '@osm_version', version
           ) || tags
       )::text, ',' ORDER BY id), 'UTF8') AS content
FROM batch
