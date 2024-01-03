from fastapi import Request

from config import VERSION


async def version_middleware(request: Request, call_next):
    response = await call_next(request)
    response.headers['X-Version'] = VERSION
    return response
