# OpenAedMap - backend

development in progress - this code is not working yet

This project contains backend for [openaedmap.org](openaedmap.org)

Functionality:
- download data and updates from OSM
- serve info about specific elements
- create and serve vector tiles
- generate files with data for download (including split per country)

## Local Development

### Run development
```
docker-compose -f docker-compose-dev.yml -p dev up
```

### Run production
```
PORT=80 docker-compose -f docker-compose-prod.yml -p prod up -d
```

### Migrations

After changing models run:
```
alembic revision --autogenerate -m "model changed"
```
