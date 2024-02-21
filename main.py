import importlib
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import timedelta

from anyio import create_task_group
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import DEFAULT_CACHE_MAX_AGE, DEFAULT_CACHE_STALE, startup_setup
from middlewares.cache_middleware import CacheMiddleware
from middlewares.profiler_middleware import ProfilerMiddleware
from middlewares.version_middleware import VersionMiddleware
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
app.add_middleware(ProfilerMiddleware)
app.add_middleware(VersionMiddleware)
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


def _make_router(path: str, prefix: str) -> APIRouter:
    """
    Create a router from all modules in the given path.
    """
    router = APIRouter(prefix=prefix)
    counter = 0

    for p in pathlib.Path(path).glob('*.py'):
        module_name = p.with_suffix('').as_posix().replace('/', '.')
        module = importlib.import_module(module_name)
        router_attr = getattr(module, 'router', None)

        if router_attr is not None:
            router.include_router(router_attr)
            counter += 1
        else:
            logging.warning('Missing router in %s', module_name)

    logging.info('Loaded %d routers from %s as %r', counter, path, prefix)
    return router


app.include_router(_make_router('api/v1', '/api/v1'))
