# OpenAEDMap Backend 🌍❤️

Ever wanted to be a superhero?
OpenAEDMap gives you that chance.
By pinpointing AED locations via OpenStreetMap, we equip you with the power to save lives.
Save a life today by visiting [OpenAEDMap.org](https://openaedmap.org).

**🔧 This repository hosts the backend code.**

For frontend implementations, please see [openaedmap-frontend](https://github.com/openstreetmap-polska/openaedmap-frontend).

### 🌟 Core Features

- **Automated OSM Updates:** Downloads data and updates from OpenStreetMap.
- **AED Information:** Serve comprehensive details about specific AED locations.
- **Vector Tiles:** Create and serve vector tiles for map rendering.
- **Regional GeoJSON:** Generate downloadable GeoJSON files by region.
- **Photo Integration:** Allows uploading and viewing of photos of individual AEDs.

### 🌐 Deployed Instances

- **Production** — main branch: [openaedmap.org](https://openaedmap.org)
- **Development** — dev branch: [dev.openaedmap.org](https://dev.openaedmap.org)

## 🛠️ Local Development

### Getting Started

Before you jump in, make sure to install the [❄️ Nix](https://nixos.org/download) package manager.
It's your shortcut to seamless dependency management and reproducible environment setup.
It will save you lots of time and spare you from unnecessary stress.

```sh
# Install dependencies and enter shell
nix-shell

# Start up the database
dev-start

# Launch the web server
h2corn main:app
```

Navigate to http://localhost:8000 to access the web app locally.

The server uses one origin process with PostgreSQL/PostGIS. Startup applies `schema.sql` and starts
country and AED synchronization. Uploaded photos live in `data/photos`; keep both that directory
and the PostgreSQL data volume when replacing an installation.

The container uses PostgreSQL 18. Before switching an existing PostgreSQL 17 installation, stop its
containers and run `bash scripts/upgrade-postgres.sh OLD_IMAGE NEW_IMAGE` from the installation
directory. Use the old image's immutable ID, captured before loading its replacement. The script
restores roles and data, compares every application table, applies the schema and analyzes the new
cluster before publishing `data/postgres18`. It retains `data/postgres17` and the logical backup.
The deployment workflow performs this conversion when needed.

`X-Version`, the user agent, and OSM edit metadata identify the Git revision automatically.
Local edits add a `-dirty` suffix; built images embed their source revision.

### Tests

Inside the development shell, run `uv run pytest`.
The suite starts disposable PostgreSQL/PostGIS and cleans it up afterward.

### Cleanup

```sh
# Terminate the database
dev-stop

# Purge database
dev-clean
```
