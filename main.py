from contextlib import asynccontextmanager
from datetime import timedelta

from anyio import create_task_group
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import api.v1 as v1
from config import DEFAULT_CACHE_MAX_AGE, DEFAULT_CACHE_STALE, startup_setup
from middlewares.cache_middleware import CacheMiddleware
from middlewares.profiler_middleware import profiler_middleware
from middlewares.version_middleware import version_middleware
from orjson_response import CustomORJSONResponse
from states.aed_state import AEDState
from states.country_state import CountryState
from states.worker_state import WorkerState, WorkerStateEnum


@asynccontextmanager
async def lifespan(_):
    worker_state = WorkerState()
    await worker_state.ainit()

    if worker_state.is_primary:
        await startup_setup()
        async with create_task_group() as tg:
            await tg.start(CountryState.update_db_task)
            await tg.start(AEDState.update_db_task)

            await worker_state.set_state(WorkerStateEnum.RUNNING)
            yield

            # on shutdown, always abort the tasks
            tg.cancel_scope.cancel()
    else:
        await worker_state.wait_for_state(WorkerStateEnum.RUNNING)
        yield


app = FastAPI(lifespan=lifespan, default_response_class=CustomORJSONResponse)
app.include_router(v1.router)
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
    allow_headers=['baggage', 'sentry-trace'],
    allow_methods=['GET'],
    max_age=int(timedelta(days=1).total_seconds()),
)
