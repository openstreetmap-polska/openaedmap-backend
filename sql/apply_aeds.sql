WITH applied AS (
    INSERT INTO aed (id, version, tags, position, country_codes)
    SELECT id, version, tags,
           CASE WHEN tags IS NOT NULL THEN ST_SetSRID(ST_MakePoint(lon, lat), 4326) END,
           ARRAY(SELECT code FROM country
                 WHERE tags IS NOT NULL
                   AND ST_Intersects(geometry, ST_SetSRID(ST_MakePoint(lon, lat), 4326))
                 ORDER BY code)
    FROM incoming
    WHERE tags IS NOT NULL OR EXISTS (SELECT FROM aed WHERE aed.id = incoming.id)
    ON CONFLICT (id) DO UPDATE SET version = EXCLUDED.version, tags = EXCLUDED.tags,
        position = EXCLUDED.position, country_codes = EXCLUDED.country_codes
        WHERE EXCLUDED.version > aed.version
    RETURNING WITH (OLD AS before, NEW AS after)
        before.tags IS NOT NULL OR after.tags IS NOT NULL AS content_changed,
        CASE WHEN before.position IS NOT NULL THEN point(ST_X(before.position), ST_Y(before.position)) END AS before,
        CASE WHEN after.position IS NOT NULL THEN point(ST_X(after.position), ST_Y(after.position)) END AS after,
        (before.position, coalesce(before.tags->>'access', ''))
            IS DISTINCT FROM (after.position, coalesce(after.tags->>'access', '')) AS render_changed,
        (before.tags IS NOT NULL) IS DISTINCT FROM (after.tags IS NOT NULL)
            OR coalesce(before.country_codes, '{}') IS DISTINCT FROM after.country_codes AS counts_changed
)
SELECT before, after, render_changed, counts_changed
FROM applied WHERE $1 AND content_changed
