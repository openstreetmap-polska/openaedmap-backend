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
# Install dependencies and packages
nix-shell

# Start up the database
make dev-start

# Launch the web server
uvicorn main:app
```

Navigate to http://localhost:8000 to access the web app locally.

### Cleanup

```sh
# Terminate the database
make dev-stop

# Purge data
rm -r data
```
