import os
from datetime import timedelta

import pymongo
from motor.core import AgnosticDatabase
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel
from pyproj import Transformer

NAME = 'openaedmap-backend'
VERSION = '2.0'
VERSION_TIMESTAMP = 0
WEBSITE = 'https://openaedmap.org'
USER_AGENT = f'{NAME}/{VERSION} (+{WEBSITE})'

OVERPASS_API_URL = 'https://overpass-api.de/api/interpreter'
REPLICATION_URL = 'https://planet.openstreetmap.org/replication/minute/'

DEFAULT_CACHE_MAX_AGE = timedelta(minutes=1)
DEFAULT_CACHE_STALE = timedelta(minutes=5)

COUNTRY_UPDATE_DELAY = timedelta(days=1)
AED_UPDATE_DELAY = timedelta(seconds=30)
AED_REBUILD_THRESHOLD = timedelta(hours=1)

TILE_COUNTRIES_CACHE_MAX_AGE = timedelta(hours=4)
TILE_CACHE_STALE = timedelta(days=7)

TILE_COUNTRIES_MAX_Z = 5
TILE_MIN_Z = 3
TILE_MAX_Z = 16

OSM_PROJ = 'epsg:4326'
MVT_PROJ = 'epsg:3857'
MVT_EXTENT = 4096
MVT_TRANSFORMER = Transformer.from_crs(OSM_PROJ, MVT_PROJ, always_xy=True)

MONGO_HOST = os.getenv('MONGO_HOST', '127.0.0.1')
MONGO_PORT = int(os.getenv('MONGO_PORT', '27017'))
MONGO_CLIENT = AsyncIOMotorClient(f'mongodb://{MONGO_HOST}:{MONGO_PORT}/?replicaSet=rs0')
_mongo_db: AgnosticDatabase = MONGO_CLIENT[NAME]

STATE_COLLECTION = _mongo_db['state']
COUNTRY_COLLECTION = _mongo_db['country']
AED_COLLECTION = _mongo_db['aed']


# this is run by a single, primary worker on startup
async def startup_setup() -> None:
    try:
        await COUNTRY_COLLECTION.drop_index([('code', pymongo.ASCENDING)])
    except Exception:
        pass

    await COUNTRY_COLLECTION.create_indexes([
        IndexModel([('geometry', pymongo.GEOSPHERE)]),
    ])

    await AED_COLLECTION.create_indexes([
        IndexModel([('id', pymongo.ASCENDING)], unique=True),
        IndexModel([('country_codes', pymongo.ASCENDING)]),
        IndexModel([('position', pymongo.GEOSPHERE)]),
    ])
