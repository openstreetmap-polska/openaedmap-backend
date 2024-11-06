import importlib
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import timedelta

from anyio import create_task_group
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from starlette_compress import CompressMiddleware

from db import db_write
from json_response import JSONResponseUTF8
from middlewares.cache_control_middleware import CacheControlMiddleware
from middlewares.cache_response_middleware import CacheResponseMiddleware
from middlewares.profiler_middleware import ProfilerMiddleware
from middlewares.version_middleware import VersionMiddleware
from services.aed_service import AEDService
from services.country_service import CountryService
from services.worker_service import WorkerService


@asynccontextmanager
async def lifespan(_):
    worker_state = await WorkerService.init()

    if worker_state.is_primary:
        async with db_write() as session:
            await session.connection(execution_options={'isolation_level': 'AUTOCOMMIT'})
            await session.execute(text('VACUUM ANALYZE'))

        async with create_task_group() as tg:
            await tg.start(CountryService.update_db_task)
            await tg.start(AEDService.update_db_task)

            await worker_state.set_state('running')
            yield

            # on shutdown, always abort the tasks
            tg.cancel_scope.cancel()
    else:
        await worker_state.wait_for_state('running')
        yield


app = FastAPI(lifespan=lifespan, default_response_class=JSONResponseUTF8)
app.add_middleware(CacheControlMiddleware)
app.add_middleware(CacheResponseMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_headers=['baggage', 'sentry-trace'],
    allow_methods=['GET'],
    max_age=int(timedelta(days=1).total_seconds()),
)
app.add_middleware(VersionMiddleware)
app.add_middleware(CompressMiddleware)
app.add_middleware(ProfilerMiddleware)


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
