CREATE OR REPLACE FUNCTION mvt (IN z int, IN x int, IN y int) RETURNS bytea
AS $$
    DECLARE
        bbox geometry;
        base double precision := 150;
    BEGIN
        bbox := ST_TileEnvelope(z, x, y);

        if z <= 5 then
            RETURN mvt_countries(bbox);
        elsif z = 6 then
            RETURN mvt_clustered(bbox, 1000 + base * 2 ^ 6);
        elsif z = 7 then
            RETURN mvt_clustered(bbox, base * 2 ^ 6);
        elsif z = 8 then
            RETURN mvt_clustered(bbox, base * 2 ^ 5);
        elsif z = 9 then
            RETURN mvt_clustered(bbox, base * 2 ^ 4);
        elsif z = 10 then
            RETURN mvt_clustered(bbox, base * 2 ^ 3);
        elsif z = 11 then
            RETURN mvt_clustered(bbox, 150 + base * 2 ^ 2);
        elsif z = 12 then
            RETURN mvt_clustered(bbox, 100 + base * 2 ^ 1);
        elsif z >= 13 and z <= 23 then
            RETURN mvt_unclustered(bbox);
        else
            raise notice 'Zoom % outside valid range.', z;
            RETURN null;
        end if;
    END;
$$ LANGUAGE plpgsql STABLE ;
