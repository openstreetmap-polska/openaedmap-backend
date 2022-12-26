# OpenAedMap - backend

development in progress - some stuff works but api is not stable, expect major changes

This project contains backend for [openaedmap.org](openaedmap.org) , [frontend-repo](https://github.com/openstreetmap-polska/openaedmap-frontend)

Functionality:
- download data and updates from OSM
- serve info about specific elements
- create and serve vector tiles
- generate files with data for download (including split per country)

Prod (main branch): https://openaedmap.openstreetmap.org.pl/docs

Dev (dev branch): https://openaedmap-dev.openstreetmap.org.pl/docs

## Local Development

### Example `.env` file
Create `.env` file in root dir
```
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=db
POSTGRES_USER=test
POSTGRES_PASSWORD=test123
DATA_FILES_DIR=./.data-files
```

!! Make sure that folder specified in `DATA_FILES_DIR` exists. !!

### Run development
```
docker-compose -f docker-compose-dev.yml -p dev up --build
```

Go to: http://localhost:8080/docs

### Run production
```
PORT=80 docker-compose -f docker-compose-prod.yml -p prod up -d
```

### Log into database
```
docker exec -it dev_db_1 psql -h localhost -U <user from .env> -d <db from .env>
```

### Migrations

After changing models
connect to container:
```
docker exec -it dev_backend_1 bash
```
and run:
```
alembic revision --autogenerate -m "model changed"
```

If the file is created as root then change owner to your user and chmod 664.

### Dependencies

If you change dependencies in `requirements.txt` then you need to run docker-compose command with `--build` flag.
