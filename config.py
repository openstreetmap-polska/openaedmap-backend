import os
from datetime import timedelta

import pymongo
from anyio import Path
from motor.core import AgnosticDatabase
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import IndexModel
from pyproj import Transformer

NAME = 'openaedmap-backend'
VERSION = '2.3'
VERSION_TIMESTAMP = 0
WEBSITE = 'https://openaedmap.org'
USER_AGENT = f'{NAME}/{VERSION} (+{WEBSITE})'

OVERPASS_API_URL = 'https://overpass-api.de/api/interpreter'
OPENSTREETMAP_API_URL = os.getenv('OPENSTREETMAP_API_URL', 'https://api.openstreetmap.org/api/0.6/')
REPLICATION_URL = 'https://planet.openstreetmap.org/replication/minute/'
COUNTRIES_GEOJSON_URL = 'https://raw.githubusercontent.com/Zaczero/osm-countries-geojson/main/geojson/osm-countries-0-01.geojson.br'

DEFAULT_CACHE_MAX_AGE = timedelta(minutes=1)
DEFAULT_CACHE_STALE = timedelta(minutes=5)

COUNTRY_UPDATE_DELAY = timedelta(days=float(os.getenv('COUNTRY_UPDATE_DELAY', '1')))
AED_UPDATE_DELAY = timedelta(seconds=30)
AED_REBUILD_THRESHOLD = timedelta(hours=1)

PLANET_DIFF_TIMEOUT = timedelta(minutes=5)

TILE_COUNTRIES_CACHE_MAX_AGE = timedelta(hours=4)
TILE_COUNTRIES_CACHE_STALE = timedelta(days=7)
TILE_AEDS_CACHE_STALE = timedelta(days=3)

TILE_COUNTRIES_MAX_Z = 5
TILE_MIN_Z = 3
TILE_MAX_Z = 16

OSM_PROJ = 'epsg:4326'
MVT_PROJ = 'epsg:3857'
MVT_EXTENT = 4096
MVT_TRANSFORMER = Transformer.from_crs(OSM_PROJ, MVT_PROJ, always_xy=True)

IMAGE_LIMIT_PIXELS = 6 * 1000 * 1000  # 6 MP (e.g., 3000x2000)
IMAGE_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB

DATA_DIR = Path('data')
PHOTOS_DIR = DATA_DIR / 'photos'

MONGO_HOST = os.getenv('MONGO_HOST', '127.0.0.1')
MONGO_PORT = int(os.getenv('MONGO_PORT', '27017'))
MONGO_CLIENT = AsyncIOMotorClient(f'mongodb://{MONGO_HOST}:{MONGO_PORT}/?replicaSet=rs0')
_mongo_db: AgnosticDatabase = MONGO_CLIENT[NAME]

STATE_COLLECTION = _mongo_db['state']
COUNTRY_COLLECTION = _mongo_db['country']
AED_COLLECTION = _mongo_db['aed']
PHOTO_COLLECTION = _mongo_db['photo']
PHOTO_REPORT_COLLECTION = _mongo_db['photo_report']


# this is run by a single, primary worker on startup
async def startup_setup() -> None:
    await DATA_DIR.mkdir(exist_ok=True)
    await PHOTOS_DIR.mkdir(exist_ok=True)

    await COUNTRY_COLLECTION.create_indexes([
        IndexModel([('geometry', pymongo.GEOSPHERE)]),
    ])

    await AED_COLLECTION.create_indexes([
        IndexModel([('id', pymongo.ASCENDING)], unique=True),
        IndexModel([('country_codes', pymongo.ASCENDING)]),
        IndexModel([('position', pymongo.GEOSPHERE)]),
    ])

    await PHOTO_COLLECTION.create_indexes([
        IndexModel([('id', pymongo.ASCENDING)], unique=True),
        IndexModel([('node_id', pymongo.ASCENDING), ('timestamp', pymongo.DESCENDING)]),
    ])

    await PHOTO_REPORT_COLLECTION.create_indexes([
        IndexModel([('photo_id', pymongo.ASCENDING)], unique=True),
        IndexModel([('timestamp', pymongo.DESCENDING)]),
    ])
