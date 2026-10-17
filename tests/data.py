import json


async def seed_aeds(records):
    from db import connection

    async with connection() as conn:
        await conn.executemany(
            'INSERT INTO aed (id,version,tags,position,country_codes) '
            'VALUES ($1,$2,$3::jsonb,ST_SetSRID(ST_MakePoint($4,$5),4326),$6)',
            [(id, version, json.dumps(tags), lon, lat, codes) for id, version, tags, lon, lat, codes in records],
        )
        await conn.execute('SELECT refresh_map_counts()')


async def seed_countries(records):
    from db import connection

    async with connection() as conn:
        await conn.executemany(
            'INSERT INTO country (code,names,geometry,label_position) '
            'VALUES ($1,$2::jsonb,ST_GeomFromText($3,4326),ST_SetSRID(ST_MakePoint($4,$5),4326))',
            [(code, json.dumps(names), geometry, lon, lat) for code, names, geometry, lon, lat in records],
        )
        await conn.execute('SELECT prepare_country_tiles()')
