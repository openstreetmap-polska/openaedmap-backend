from fastapi.responses import JSONResponse


class JSONResponseUTF8(JSONResponse):
    media_type = 'application/json; charset=utf-8'
