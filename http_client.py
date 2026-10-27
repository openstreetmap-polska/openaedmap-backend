import socket
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from ipaddress import IPv4Address, IPv6Address, IPv6Network, ip_address
from urllib.parse import quote

from aiohttp import ClientRequest, ClientSession, ClientTimeout, InvalidURL, TCPConnector
from yarl import URL

from config import USER_AGENT

_NAT64 = IPv6Network('64:ff9b::/96')
_client: ClientSession | None = None


class UnsafeAddressError(OSError):
    pass


def public_socket(address_info: tuple) -> socket.socket:
    address = ip_address(address_info[4][0])
    if not address.is_global or (
        isinstance(address, IPv6Address) and address in _NAT64 and not IPv4Address(address.packed[-4:]).is_global
    ):
        # Happy Eyeballs preserves the exception type when all rejected addresses have the same error.
        raise UnsafeAddressError('Address is not globally reachable')
    return socket.socket(address_info[0], address_info[1], address_info[2])


def request_url(value: str) -> URL:
    # Requoting an existing escape can invalidate a signed image URL.
    try:
        raw = URL(value, encoded=True)
        return URL.build(
            scheme=raw.scheme,
            authority=URL(value).raw_authority,
            path=quote(raw.raw_path, safe="/%:@!$&'()*+,;="),
            query_string=quote(raw.raw_query_string, safe="/?%:@!$&'()*+,;="),
            encoded=True,
        )
    except ValueError as exc:
        raise InvalidURL(value) from exc


class EncodedRequest(ClientRequest):
    def __init__(self, method, url, **kwargs):
        super().__init__(method, request_url(str(url)), **kwargs)


@asynccontextmanager
async def lifespan() -> AsyncGenerator[None]:
    global _client
    async with ClientSession(
        connector=TCPConnector(socket_factory=public_socket),
        headers={'User-Agent': USER_AGENT},
        timeout=ClientTimeout(total=None, connect=60, sock_connect=15, sock_read=60),
        request_class=EncodedRequest,
        requote_redirect_url=False,
    ) as session:
        _client = session
        try:
            yield
        finally:
            _client = None


def client() -> ClientSession:
    if _client is None:
        raise RuntimeError('HTTP client has not been opened')
    return _client
