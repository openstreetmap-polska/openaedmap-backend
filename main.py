from contextlib import asynccontextmanager
from datetime import timedelta

import anyio
import sentry_sdk
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import api.v1.api as api
from config import DEFAULT_CACHE_MAX_AGE, DEFAULT_CACHE_STALE, ENVIRONMENT, VERSION, startup_setup
from middlewares.cache_middleware import CacheMiddleware
from middlewares.profiler_middleware import profiler_middleware
from middlewares.version_middleware import version_middleware
from orjson_response import CustomORJSONResponse
from states.aed_state import get_aed_state
from states.country_state import get_country_state
from states.worker_state import WorkerStateEnum, get_worker_state

if ENVIRONMENT:
    sentry_sdk.init(
        dsn='https://40b1753c3f72721489ca0bca38bb4566@sentry.monicz.dev/3',
        release=VERSION,
        environment=ENVIRONMENT,
    )


@asynccontextmanager
async def lifespan(_):
    worker_state = get_worker_state()
    await worker_state.ainit()

    if worker_state.is_primary:
        await startup_setup()
        async with anyio.create_task_group() as tg:
            await tg.start(get_country_state().update_db_task)
            await tg.start(get_aed_state().update_db_task)

            await worker_state.set_state(WorkerStateEnum.RUNNING)
            yield

            # on shutdown, always abort the tasks
            tg.cancel_scope.cancel()
    else:
        await worker_state.wait_for_state(WorkerStateEnum.RUNNING)
        yield


app = FastAPI(lifespan=lifespan, default_response_class=CustomORJSONResponse)
app.include_router(api.router, prefix='/api/v1')
app.add_middleware(BaseHTTPMiddleware, dispatch=profiler_middleware)
app.add_middleware(BaseHTTPMiddleware, dispatch=version_middleware)
app.add_middleware(
    CacheMiddleware,
    max_age=DEFAULT_CACHE_MAX_AGE,
    stale=DEFAULT_CACHE_STALE,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['GET'],
    max_age=int(timedelta(days=1).total_seconds()),
)
