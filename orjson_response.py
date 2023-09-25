from fastapi.responses import ORJSONResponse


class CustomORJSONResponse(ORJSONResponse):
    media_type = 'application/json; charset=utf-8'
