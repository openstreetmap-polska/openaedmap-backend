from fastapi import FastAPI

from backend.api.v1.api import api_router
from backend.core.config import settings

app = FastAPI()
app.include_router(api_router, prefix=settings.API_V1_STR)
