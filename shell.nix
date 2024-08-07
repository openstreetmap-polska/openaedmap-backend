{ isDevelopment ? true }:

let
  # Update packages with `nixpkgs-update` command
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/4284c2b73c8bce4b46a6adf23e16d9e2ec8da4bb.tar.gz") { };

  pythonLibs = with pkgs; [
    stdenv.cc.cc.lib
    file.out
    libxml2.out
    zlib.out
  ];
  wrappedPython = with pkgs; (symlinkJoin {
    name = "python";
    paths = [
      # Enable compiler optimizations when in production
      (if isDevelopment then python312 else python312.override { enableOptimizations = true; })
    ];
    buildInputs = [ makeWrapper ];
    postBuild = ''
      wrapProgram "$out/bin/python3.12" --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath pythonLibs}"
    '';
  });

  packages' = with pkgs; [
    wrappedPython
    poetry
    ruff
    coreutils
    (postgresql_16_jit.withPackages (ps: [ ps.postgis ]))
    valkey

    # Scripts
    # -- Alembic
    (writeShellScriptBin "alembic-migration" ''
      set -e
      name=$1
      if [ -z "$name" ]; then
        read -p "Database migration name: " name
      fi
      alembic -c config/alembic.ini revision --autogenerate --message "$name"
    '')
    (writeShellScriptBin "alembic-upgrade" "alembic -c config/alembic.ini upgrade head")

    # -- Supervisor
    (writeShellScriptBin "dev-start" ''
      set -e
      pid=$(cat data/supervisor/supervisord.pid 2> /dev/null || echo "")
      if [ -n "$pid" ] && $(grep -q "supervisord" "/proc/$pid/cmdline" 2> /dev/null); then
        echo "Supervisor is already running"
        exit 0
      fi

      if [ ! -f data/postgres/PG_VERSION ]; then
        initdb -D data/postgres \
          --no-instructions \
          --locale-provider=icu \
          --icu-locale=und \
          --no-locale \
          --text-search-config=pg_catalog.simple \
          --auth=password \
          --username=postgres \
          --pwfile=<(echo postgres)
      fi

      mkdir -p /tmp/openaedmap-postgres data/supervisor
      supervisord -c config/supervisord.conf
      echo "Supervisor started"

      echo "Waiting for Postgres to start..."
      time_start=$(date +%s)
      while ! pg_isready -q -h /tmp/openaedmap-postgres; do
        elapsed=$(($(date +%s) - $time_start))
        if [ $elapsed -gt 10 ]; then
          tail -n 15 data/supervisor/supervisord.log data/supervisor/postgres.log
          echo "Postgres startup timeout, see above logs for details"
          dev-stop
          exit 1
        fi
        sleep 0.1
      done

      echo "Postgres started, running migrations"
      alembic-upgrade
    '')
    (writeShellScriptBin "dev-stop" ''
      set -e
      pid=$(cat data/supervisor/supervisord.pid 2> /dev/null || echo "")
      if [ -n "$pid" ] && $(grep -q "supervisord" "/proc/$pid/cmdline" 2> /dev/null); then
        kill -INT "$pid"
        echo "Supervisor stopping..."
        while $(kill -0 "$pid" 2> /dev/null); do sleep 0.1; done
        echo "Supervisor stopped"
      else
        echo "Supervisor is not running"
      fi
    '')
    (writeShellScriptBin "dev-restart" ''
      set -ex
      dev-stop
      dev-start
    '')
    (writeShellScriptBin "dev-clean" ''
      set -e
      dev-stop
      rm -rf data/postgres
    '')
    (writeShellScriptBin "dev-logs-postgres" "tail -f data/supervisor/postgres.log")

    # -- Misc
    (writeShellScriptBin "make-version" ''
      sed -i -r "s|VERSION = '([0-9.]+)'|VERSION = '\1.$(date +%y%m%d)'|g" config.py
    '')
    (writeShellScriptBin "nixpkgs-update" ''
      set -e
      hash=$(git ls-remote https://github.com/NixOS/nixpkgs nixpkgs-unstable | cut -f 1)
      sed -i -E "s|/nixpkgs/archive/[0-9a-f]{40}\.tar\.gz|/nixpkgs/archive/$hash.tar.gz|" shell.nix
      echo "Nixpkgs updated to $hash"
    '')
    (writeShellScriptBin "docker-build" ''
      set -e
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker load < "$(nix-build --no-out-link)"
    '')
  ];

  shell' = with pkgs; lib.optionalString isDevelopment ''
    current_python=$(readlink -e .venv/bin/python || echo "")
    current_python=''${current_python%/bin/*}
    [ "$current_python" != "${wrappedPython}" ] && rm -r .venv

    echo "Installing Python dependencies"
    export POETRY_VIRTUALENVS_IN_PROJECT=1
    poetry env use "${wrappedPython}/bin/python"
    poetry install --no-root --compile

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    # Development environment variables
    export PYTHONNOUSERSITE=1
    export TZ=UTC

    if [ -f .env ]; then
      echo "Loading .env file"
      set -o allexport
      source .env set
      set +o allexport
    fi
  '' + lib.optionalString (!isDevelopment) ''
    make-version
  '';
in
pkgs.mkShellNoCC {
  buildInputs = packages';
  shellHook = shell';
}
