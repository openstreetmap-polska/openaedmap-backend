from fastapi import Request
from pyinstrument import Profiler
from starlette.responses import HTMLResponse


# https://pyinstrument.readthedocs.io/en/latest/guide.html#profile-a-web-request-in-fastapi
async def profiler_middleware(request: Request, call_next):
    profiling = request.query_params.get('profile', False)
    if profiling:
        profiler = Profiler()
        profiler.start()
        await call_next(request)
        profiler.stop()
        return HTMLResponse(profiler.output_html())
    else:
        return await call_next(request)
