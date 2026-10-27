from contextlib import asynccontextmanager

import pytest
from aiohttp import ClientSession
from aiointercept import aiointercept


@asynccontextmanager
async def mock_http(*, middlewares=()):
    import http_client

    async with (
        aiointercept(mock_external_urls=True) as upstream,
        ClientSession(middlewares=middlewares) as session,
    ):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(http_client, 'client', lambda: session)
            yield upstream
