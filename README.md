# OpenAEDMap - backend

This is the backend repository for [OpenAEDMap.org](https://openaedmap.org). For the frontend code, please visit the [openaedmap-frontend](https://github.com/openstreetmap-polska/openaedmap-frontend).

Primary functionality:

- download data and updates from OSM
- serve information about specific AEDs
- create and serve vector tiles
- generate regional geojson files for download

### Deployed instances

<kbd>PROD</kbd> (main branch): https://openaedmap.org

<kbd>DEV</kbd> (dev branch): https://dev.openaedmap.org

## Local Development

### Getting started

Before proceeding, install [nix](https://nixos.org/download) package manager. It simplifies installation of dependencies and setting up environment.

```sh
# install dependencies, packages, etc.
nix-shell

# start database
make dev-start

# start web server
uvicorn main:app
```

You can access the web app at: http://localhost:8000.

### Cleaning up

```sh
# stop database
make dev-stop

# delete data
rm -r data
```
