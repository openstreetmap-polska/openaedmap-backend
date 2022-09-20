from pydantic import BaseSettings

from os import environ


class Settings(BaseSettings):
    API_V1_STR: str = '/api/v1'
    PROJECT_NAME: str = 'Open AED Map'
    DATABASE_URL: str = 'postgresql://{}:{}@{}/{}'.format(
        environ.get('POSTGRES_USER'),
        environ.get('POSTGRES_PASSWORD'),
        environ.get('POSTGRES_HOST'),
        environ.get('POSTGRES_DB')
    )


settings = Settings()
