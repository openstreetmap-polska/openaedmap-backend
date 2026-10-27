import asyncio
import os
import socket
import subprocess
import sys
from compression.zstd import compress
from pathlib import Path

import aiohttp
import httpx
import pytest
from fastapi import FastAPI

from http_client import EncodedRequest, UnsafeAddressError, public_socket, request_url


@pytest.mark.parametrize(
    'address',
    [
        '127.0.0.1',
        '10.0.0.1',
        '169.254.169.254',
        '192.168.1.1',
        '::1',
        'fe80::1',
        '::ffff:127.0.0.1',
        '64:ff9b::7f00:1',
    ],
)
def test_socket_policy_rejects_non_global_addresses(address):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    with pytest.raises(UnsafeAddressError):
        public_socket((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 443)))


@pytest.mark.parametrize('address', ['1.1.1.1', '2606:4700:4700::1111'])
def test_socket_policy_accepts_global_addresses_without_connecting(address):
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    with public_socket((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 443))) as sock:
        assert sock.family == family


@pytest.mark.parametrize(
    ('value', 'expected'),
    [
        ('https://example.com/a%2Fb?key=a%2Fb&space=%20', 'https://example.com/a%2Fb?key=a%2Fb&space=%20'),
        ('https://example.com/café?key=a%2Fb', 'https://example.com/caf%C3%A9?key=a%2Fb'),
        ('https://münich.example/café?name=été', 'https://xn--mnich-kva.example/caf%C3%A9?name=%C3%A9t%C3%A9'),
        ('https://example.com/a b?x=first&x=second+value#fragment', 'https://example.com/a%20b?x=first&x=second+value'),
    ],
    ids=('signed-query', 'unicode-path', 'international-host', 'repeated-query-and-space'),
)
def test_request_urls_preserve_encoded_values_and_encode_international_text(value, expected):
    assert str(request_url(value)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('host', ['127.0.0.1', 'localhost'])
async def test_photo_proxy_blocks_private_connections_before_sending_a_request(host):
    from api.v1.photos import router

    received = []

    async def serve(reader, writer):
        try:
            received.append(await reader.readuntil(b'\r\n\r\n'))
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    async with (
        asyncio.TaskGroup() as tasks,
        await asyncio.start_server(
            lambda reader, writer: tasks.create_task(serve(reader, writer)), '127.0.0.1', 0
        ) as server,
    ):
        port = server.sockets[0].getsockname()[1]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.get(f'/api/v1/photos/proxy/direct/http://{host}:{port}/private')
    assert response.status_code == 400
    assert response.headers['cache-control'] == 'no-store'
    assert received == []


@pytest.mark.asyncio
@pytest.mark.parametrize('redirect', [False, True])
async def test_client_preserves_signed_target_and_decodes_zstd_streams(redirect):
    payload = b'country-data' * 10000
    encoded = compress(payload)
    received = []

    async def serve(reader, writer):
        try:
            received.append(await reader.readuntil(b'\r\n\r\n'))
            if redirect and len(received) == 1:
                writer.write(
                    'HTTP/1.1 302 Found\r\nLocation: /café?key=a%2Fb&key=second\r\nContent-Length: 0\r\n\r\n'.encode()
                )
                await writer.drain()
                return
            writer.write(
                f'HTTP/1.1 200 OK\r\nContent-Encoding: zstd\r\nContent-Length: {len(encoded)}\r\n\r\n'.encode()
            )
            writer.write(encoded)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async with (
        asyncio.TaskGroup() as tasks,
        await asyncio.start_server(
            lambda reader, writer: tasks.create_task(serve(reader, writer)), '127.0.0.1', 0
        ) as server,
    ):
        port = server.sockets[0].getsockname()[1]
        async with (
            aiohttp.ClientSession(request_class=EncodedRequest, requote_redirect_url=False) as client,
            client.get(request_url(f'http://127.0.0.1:{port}/a%2Fb?key=a%2Fb&key=second')) as response,
        ):
            chunks = [chunk async for chunk in response.content.iter_chunked(8192)]
    assert b''.join(chunks) == payload
    assert received[0].startswith(b'GET /a%2Fb?key=a%2Fb&key=second HTTP/1.1\r\n')
    if redirect:
        assert received[1].startswith(b'GET /caf%C3%A9?key=a%2Fb&key=second HTTP/1.1\r\n')


def test_http_session_is_owned_by_application_lifespan():
    source = """
import asyncio
import http_client

async def main():
    async with http_client.lifespan():
        client = http_client.client()
        assert not client.closed
    assert client.closed
    try:
        http_client.client()
    except RuntimeError:
        pass
    else:
        raise AssertionError('Closed client remained available')

asyncio.run(main())
"""
    subprocess.run(  # noqa: S603 - fixed test program using this environment's interpreter
        [sys.executable, '-c', source],
        env={**os.environ, 'SENTRY_DSN': '', 'PYTHONPATH': str(Path(__file__).parent.parent)},
        check=True,
    )
