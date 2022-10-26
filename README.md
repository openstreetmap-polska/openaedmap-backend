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
docker-compose -f docker-compose-dev.yml -p dev up --build
```

Go to: http://localhost:8080/docs

### Run production
```
PORT=80 docker-compose -f docker-compose-prod.yml -p prod up -d
```

### Migrations

After changing models
connect to container:
```
docker exec dev_backend_1 -it bash
```
and run:
```
alembic revision --autogenerate -m "model changed"
```

If the file is created as root then change owner to your user and chmod 664.

### Dependencies

If you change dependencies in `requirements.txt` then you need to run docker-compose command with `--build` flag.
