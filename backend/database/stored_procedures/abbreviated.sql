CREATE OR REPLACE FUNCTION abbreviated (IN number bigint) RETURNS text
AS  $$
    SELECT CASE
      WHEN number > 999999 THEN ROUND(number/1000000.0, 1)::text || 'm'
      WHEN number > 999 THEN ROUND(number/1000.0, 1)::text || 'k'
      ELSE number::text
    END
$$ LANGUAGE SQL IMMUTABLE PARALLEL SAFE ;
