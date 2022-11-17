from sqlalchemy.orm import Session


def get_vector_tile(z: int, x: int, y: int, db: Session) -> bytes:
    base = 150

    match z:
        case z if z <= 5:
            return vector_tile_by_country(z, x, y, db)
        case 6:
            return vector_tile_clustered(z, x, y, base * 2 ** 7, db)
        case 7:
            return vector_tile_clustered(z, x, y, base * 2 ** 6, db)
        case 8:
            return vector_tile_clustered(z, x, y, base * 2 ** 5, db)
        case 9:
            return vector_tile_clustered(z, x, y, base * 2 ** 4, db)
        case 10:
            return vector_tile_clustered(z, x, y, base * 2 ** 3, db)
        case 11:
            return vector_tile_clustered(z, x, y, base * 2 ** 2, db)
        case 12:
            return vector_tile_clustered(z, x, y, base * 2 ** 1, db)
        case 13:
            return vector_tile_unclustered(z, x, y, db)
        case _:
            return b""


def vector_tile_unclustered(z: int, x: int, y: int, db: Session) -> bytes:
    return db.scalar(
        """
        WITH
            nodes as (
                SELECT
                    ST_AsMVTGeom(ST_Transform(geometry, 3857), ST_TileEnvelope(:z, :x, :y)) as geom,
                    node_id,
                    tags ->> 'access' as access
                FROM osm_nodes
                WHERE ST_Intersects(geometry, ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326))
            )
            SELECT ST_AsMVT(nodes.*, 'defibrillators')
            FROM nodes
        """,
        params={"z": z, "x": x, "y": y}
    ).tobytes()


def vector_tile_clustered(z: int, x: int, y: int, cluster_range: int, db: Session) -> bytes:
    return db.scalar(
        """
        WITH
            nodes as (
                SELECT
                    ST_Transform(geometry, 3857) geom,
                    node_id,
                    tags ->> 'access' as access
                FROM osm_nodes
                WHERE ST_Intersects(geometry, ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326))
            ),
            assigned_cluster_id as (
                SELECT
                    ST_ClusterDBSCAN(geom, eps := :cluster_range, minpoints := 2) over () AS cluster_id,
                    nodes.*
                FROM nodes
            ),
            clustered as (
                SELECT
                    ST_AsMVTGeom(ST_GeometricMedian(ST_Union(geom)), ST_TileEnvelope(:z, :x, :y)),
                    COUNT(*) as point_count,
                    CASE
                      WHEN COUNT(*) > 999 THEN ROUND(COUNT(*)/1000.0, 1)::text || 'k'
                      ELSE COUNT(*)::text
                    END point_count_abbreviated,
                    null::bigint as node_id,
                    null::text as access
                FROM assigned_cluster_id
                WHERE cluster_id IS NOT NULL
                GROUP BY cluster_id
                UNION ALL
                SELECT
                    ST_AsMVTGeom(geom, ST_TileEnvelope(:z, :x, :y)),
                    null,
                    null,
                    node_id,
                    access
                FROM assigned_cluster_id
                WHERE cluster_id IS NULL
            )
            SELECT ST_AsMVT(clustered.*, 'defibrillators')
            FROM clustered
        """,
        params={"z": z, "x": x, "y": y, "cluster_range": cluster_range}
    ).tobytes()


def vector_tile_by_country(z: int, x: int, y: int, db: Session) -> bytes:
    return db.scalar(
        """
        WITH
            c as (
                SELECT
                    ST_AsMVTGeom(ST_Transform(label_point, 3857), ST_TileEnvelope(:z, :x, :y)),
                    country_code,
                    feature_count as point_count,
                    CASE
                      WHEN feature_count > 999 THEN ROUND(feature_count/1000.0, 1)::text || 'k'
                      ELSE feature_count::text
                    END point_count_abbreviated
                FROM countries
                WHERE ST_Intersects(geometry, ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326))
            )
            SELECT ST_AsMVT(c.*, 'defibrillators')
            FROM c
        """,
        params={"z": z, "x": x, "y": y}
    ).tobytes()
