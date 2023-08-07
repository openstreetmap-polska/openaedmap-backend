from datetime import timedelta

import anyio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse

import api.v1.api as api
from config import DEFAULT_CACHE_MAX_AGE, DEFAULT_CACHE_STALE, startup_setup
from middlewares.cache_middleware import CacheMiddleware
from middlewares.version_middleware import VersionMiddleware
from states.aed_state import get_aed_state
from states.country_state import get_country_state
from states.worker_state import WorkerStateEnum, get_worker_state

app = FastAPI(default_response_class=ORJSONResponse)
app.include_router(api.router, prefix='/api/v1')
app.add_middleware(VersionMiddleware)
app.add_middleware(
    CacheMiddleware,
    max_age=DEFAULT_CACHE_MAX_AGE,
    stale=DEFAULT_CACHE_STALE
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['GET'],
    max_age=int(timedelta(days=1).total_seconds())
)

app_tg = anyio.create_task_group()


@app.on_event('startup')
async def startup():
    worker_state = get_worker_state()
    await worker_state.ainit()

    if worker_state.is_primary:
        await startup_setup()
        await app_tg.__aenter__()
        async with anyio.create_task_group() as tg:
            tg.start_soon(app_tg.start, get_country_state().update_db_task)
            tg.start_soon(app_tg.start, get_aed_state().update_db_task)
        await worker_state.set_state(WorkerStateEnum.RUNNING)
    else:
        await worker_state.wait_for_state(WorkerStateEnum.RUNNING)


@app.on_event('shutdown')
async def shutdown():
    worker_state = get_worker_state()

    if worker_state.is_primary:
        await app_tg.cancel_scope.cancel()
