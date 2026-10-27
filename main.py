from asyncio import Event, TaskGroup
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette_compress import CompressMiddleware

from api.v1 import countries, node, photos, tile
from db import close_database, open_database
from json_response import JSONResponseUTF8
from middlewares.cache_control_middleware import CacheControlMiddleware
from middlewares.version_middleware import VersionMiddleware
from services.aed_service import AEDService
from services.byte_cache import byte_cache
from services.country_service import CountryService
from utils import HTTP


@asynccontextmanager
async def lifespan(_):
    await open_database()
    try:
        async with TaskGroup() as tg:
            country_started = Event()
            tg.create_task(CountryService.update_db_task(country_started))
            await country_started.wait()
            aed_started = Event()
            tg.create_task(AEDService.update_db_task(aed_started))
            await aed_started.wait()

            yield

            tg.cancel()
    finally:
        await byte_cache.close()
        await close_database()
        await HTTP.aclose()


app = FastAPI(lifespan=lifespan, default_response_class=JSONResponseUTF8)
app.add_middleware(CacheControlMiddleware)
app.add_middleware(CompressMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_headers=['baggage', 'sentry-trace'],
    allow_methods=['GET'],
    max_age=int(timedelta(days=1).total_seconds()),
)
app.add_middleware(VersionMiddleware)
for router in (countries.router, node.router, photos.router, tile.router):
    app.include_router(router, prefix='/api/v1')
