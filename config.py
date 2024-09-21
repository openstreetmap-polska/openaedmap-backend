import os
from datetime import timedelta
from logging.config import dictConfig

import sentry_sdk
from anyio import Path
from pyproj import Transformer

NAME = 'openaedmap-backend'
VERSION = '2.11.0'
CREATED_BY = f'{NAME} {VERSION}'
WEBSITE = 'https://openaedmap.org'

USER_AGENT = f'{NAME}/{VERSION} (+{WEBSITE})'
ENVIRONMENT = os.getenv('ENVIRONMENT')
LOG_LEVEL = 'DEBUG'

POSTGRES_LOG = os.getenv('POSTGRES_LOG', '0').strip().lower() in ('1', 'true', 'yes')
POSTGRES_URL = 'postgresql+asyncpg://postgres:postgres@/postgres?host=/tmp/openaedmap-postgres'
VALKEY_URL = os.getenv('VALKEY_URL', 'unix:///tmp/openaedmap-valkey.sock?protocol=3')

DEFAULT_CACHE_MAX_AGE = timedelta(minutes=1)
DEFAULT_CACHE_STALE = timedelta(minutes=5)

COUNTRY_GEOJSON_URL = 'https://osm-countries-geojson.monicz.dev/osm-countries-0-01.geojson.zst'
COUNTRY_UPDATE_DELAY = timedelta(days=float(os.getenv('COUNTRY_UPDATE_DELAY', '1')))
AED_UPDATE_DELAY = timedelta(seconds=30)
AED_REBUILD_THRESHOLD = timedelta(hours=1)

PLANET_REPLICA_URL = 'https://planet.openstreetmap.org/replication/minute/'
PLANET_DIFF_TIMEOUT = timedelta(minutes=5)

TILE_COUNTRIES_CACHE_MAX_AGE = timedelta(hours=4)
TILE_COUNTRIES_CACHE_STALE = timedelta(days=7)
TILE_AEDS_CACHE_STALE = timedelta(days=3)

TILE_COUNTRIES_MAX_Z = 5
TILE_MIN_Z = 3
TILE_MAX_Z = 16

OVERPASS_API_URL = 'https://overpass-api.de/api/interpreter'
OPENSTREETMAP_API_URL = os.getenv('OPENSTREETMAP_API_URL', 'https://api.openstreetmap.org/api/0.6/')

DEFAULT_CHANGESET_TAGS = {
    'comment': 'Updated AED image',
    'created_by': CREATED_BY,
    'website': WEBSITE,
}

CHANGESET_ID_PLACEHOLDER = '__CHANGESET_ID_PLACEHOLDER__'

OSM_PROJ = 'epsg:4326'
MVT_PROJ = 'epsg:3857'
MVT_EXTENT = 4096
MVT_TRANSFORMER = Transformer.from_crs(OSM_PROJ, MVT_PROJ, always_xy=True)

IMAGE_CONTENT_TYPES = {'image/jpeg', 'image/jpg', 'image/png', 'image/webp'}
IMAGE_LIMIT_PIXELS = 6 * 1000 * 1000  # 6 MP (e.g., 3000x2000)
IMAGE_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
IMAGE_REMOTE_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

DATA_DIR = Path('data')
PHOTOS_DIR = Path('data/photos')

# Logging configuration
dictConfig(
    {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'default': {
                '()': 'uvicorn.logging.DefaultFormatter',
                'fmt': '%(levelprefix)s | %(asctime)s | %(name)s: %(message)s',
                'datefmt': '%Y-%m-%d %H:%M:%S',
            },
        },
        'handlers': {
            'default': {
                'formatter': 'default',
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stderr',
            },
        },
        'loggers': {
            'root': {'handlers': ['default'], 'level': LOG_LEVEL},
            **{
                # reduce logging verbosity of some modules
                module: {'handlers': [], 'level': 'INFO'}
                for module in (
                    'hpack',
                    'httpx',
                    'httpcore',
                    'multipart',
                    'PIL',
                )
            },
            **{
                # conditional database logging
                module: {'handlers': [], 'level': 'INFO'}
                for module in (
                    'sqlalchemy.engine',
                    'sqlalchemy.pool',
                )
                if POSTGRES_LOG
            },
        },
    }
)

if SENTRY_DSN := os.getenv('SENTRY_DSN'):
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        release=VERSION,
        environment=ENVIRONMENT,
        enable_tracing=True,
        traces_sample_rate=0.2,
        trace_propagation_targets=None,
        profiles_sample_rate=0.2,
    )
