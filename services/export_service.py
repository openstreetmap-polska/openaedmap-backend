import os
from asyncio import CancelledError, Semaphore, Task, create_task, shield
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from hashlib import blake2b
from pathlib import Path
from tempfile import TemporaryFile
from typing import BinaryIO

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from db import connection
from payload_response import is_not_modified
from utils import run_blocking

_QUERY = (Path(__file__).parent.parent / 'sql/export.sql').read_text()
_FORMAT = blake2b(_QUERY.encode(), digest_size=8).hexdigest()
_WORLD_QUERY = _QUERY.replace('/* COUNTRY_FILTER */', '')
_COUNTRY_QUERY = _QUERY.replace('/* COUNTRY_FILTER */', 'AND country_codes @> ARRAY[$2]::varchar(8)[]')


def _etag(country: str, revision: int) -> str:
    identity = blake2b(country.encode(), digest_size=8).hexdigest()
    return f'W/"geojson-{_FORMAT}-{identity}-{revision}"'


async def _revision() -> int:
    async with connection() as conn:
        return await conn.fetchval('SELECT revision FROM map_revision')


@dataclass(frozen=True, slots=True)
class PreparedExport:
    file: BinaryIO
    size: int
    date: datetime
    etag: str


class _Export:
    __slots__ = ('readers', 'task')

    def __init__(self, service: ExportService, country: str) -> None:
        self.readers = 0
        self.task: Task[PreparedExport] = create_task(
            service._build(country),  # noqa: SLF001 - shared lifecycle owner
            name='geojson-export',
        )


class ExportService:
    def __init__(self) -> None:
        self.jobs: dict[tuple[str, int], _Export] = {}
        self.active: set[_Export] = set()
        self.slot = Semaphore(1)

    @asynccontextmanager
    async def open(self, country: str) -> AsyncGenerator[PreparedExport]:
        key = country, await _revision()
        job = self.jobs.get(key)
        if job is None:
            if len(self.active) >= 4:
                raise HTTPException(503, 'Export capacity is busy', headers={'Retry-After': '5'})
            job = self.jobs[key] = _Export(self, country)
            self.active.add(job)
        if job.readers >= 64:
            raise HTTPException(503, 'Export capacity is busy', headers={'Retry-After': '5'})
        job.readers += 1
        try:
            yield await shield(job.task)
        finally:
            job.readers -= 1
            if job.readers == 0:
                del self.jobs[key]
                if not job.task.done():
                    job.task.cancel()
                try:
                    with suppress(CancelledError, Exception):
                        prepared = await job.task
                        await run_blocking(prepared.file.close)
                finally:
                    self.active.remove(job)

    async def _build(self, country: str) -> PreparedExport:
        async with self.slot:
            file = TemporaryFile()  # noqa: SIM115 - ownership transfers to the last reader
            date = datetime.now(UTC)
            size = 0
            query = _WORLD_QUERY if country == 'WORLD' else _COUNTRY_QUERY
            arguments = () if country == 'WORLD' else (country,)
            try:
                size += await run_blocking(file.write, b'{"type":"FeatureCollection","features":[')
                async with connection() as conn, conn.transaction(isolation='repeatable_read', readonly=True):
                    revision = await conn.fetchval('SELECT revision FROM map_revision')
                    last_id = 0
                    first = True
                    while True:
                        row = await conn.fetchrow(query, last_id, *arguments)
                        if row['count'] == 0:
                            break
                        content = row['content'] if first else b',' + row['content']
                        size += await run_blocking(file.write, content)
                        first = False
                        last_id = row['next_id']
                size += await run_blocking(file.write, b']}')
                await run_blocking(file.flush)
            except BaseException:
                await run_blocking(file.close)
                raise
            else:
                return PreparedExport(file, size, date, _etag(country, revision))


exports = ExportService()


class GeoJSONResponse(Response):
    def __init__(self, country: str) -> None:
        super().__init__(
            media_type='application/geo+json; charset=utf-8', headers={'Content-Disposition': 'attachment'}
        )
        self.country = country

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        request = Request(scope)
        etag = _etag(self.country, await _revision())
        if is_not_modified(request, etag):
            self.headers['ETag'] = etag
            self.headers['Date'] = format_datetime(datetime.now(UTC), usegmt=True)
            self.headers['Vary'] = 'Accept-Encoding'
            del self.headers['Content-Length']
            self.status_code = 304
            await super().__call__(scope, receive, send)
            return
        async with exports.open(self.country) as prepared:
            self.headers['ETag'] = prepared.etag
            self.headers['Vary'] = 'Accept-Encoding'
            self.headers['Content-Length'] = str(prepared.size)
            self.headers['Date'] = format_datetime(prepared.date, usegmt=True)
            self.headers['Age'] = str(int((datetime.now(UTC) - prepared.date).total_seconds()))
            await send({'type': 'http.response.start', 'status': 200, 'headers': self.raw_headers})
            if scope['method'] != 'HEAD':
                offset = 0
                while offset < prepared.size:
                    chunk = await run_blocking(
                        os.pread, prepared.file.fileno(), min(65536, prepared.size - offset), offset
                    )
                    if not chunk:
                        raise OSError('Prepared export ended before its declared size')
                    await send({'type': 'http.response.body', 'body': chunk, 'more_body': True})
                    offset += len(chunk)
            await send({'type': 'http.response.body', 'body': b''})
