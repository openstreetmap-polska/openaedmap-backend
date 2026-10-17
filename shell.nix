{
  isDevelopment ? true,
}:

let
  pkgs = import ./nixpkgs.nix;

  pythonLibs = with pkgs; [
    file.out
    libxml2.out
    zlib.out
    stdenv.cc.cc.lib
  ];
  python' =
    with pkgs;
    (symlinkJoin {
      name = "python";
      paths = [
        # Enable compiler optimizations when in production
        (if isDevelopment then python314 else python314.override { enableOptimizations = true; })
      ];
      buildInputs = [ makeWrapper ];
      postBuild = ''
        wrapProgram "$out/bin/python3.14" --prefix LD_LIBRARY_PATH : "${lib.makeLibraryPath pythonLibs}"
      '';
    });

  packages' =
    with pkgs;
    [
      python'
      coreutils
      gnugrep
      (lib.getBin curl)
      (postgresql_18.withPackages (ps: [
        (runCommand "postgis-runtime-${ps.postgis.version}" { } ''
          cp -a ${ps.postgis}/. "$out"
          chmod -R u+w "$out"
          # Unused without JIT; bitcode retains the build compiler's entire closure.
          rm -rf "$out/lib/bitcode"
          rm -f "$out"/bin/raster2pgsql* "$out"/lib/postgis_raster-* \
            "$out"/share/postgresql/extension/postgis_raster*
        '')
      ]))

    ]
    ++ lib.optionals isDevelopment [
      git
      nix
      uv
      ruff
      ty
      actionlint
      nixfmt
      shellcheck
      zizmor
    ]
    ++ [

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
            --auth=trust \
            --username=postgres
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

        echo "Postgres started"
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
    ]
    ++ lib.optionals isDevelopment [
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
    ]
    ++ lib.optionals isDevelopment [
      (writeShellScriptBin "nixpkgs-update" ''
        set -euo pipefail
        revision=$(curl -fsSL https://channels.nixos.org/nixpkgs-unstable/git-revision)
        [[ "$revision" =~ ^[0-9a-f]{40}$ ]]
        hash=$(nix-prefetch-url --unpack "https://github.com/NixOS/nixpkgs/archive/$revision.tar.gz")
        sed -i \
          -e "s|nixpkgs/archive/[0-9a-f]\{40\}|nixpkgs/archive/$revision|" \
          -e "s|sha256 = \"[^\"]*\"|sha256 = \"$hash\"|" nixpkgs.nix
        echo "Nixpkgs updated to $revision"
      '')
      (writeShellScriptBin "docker-build" ''
        set -e
        export SENTRY_RELEASE="$(SENTRY_RELEASE= python -c 'from version import VERSION; print(VERSION)')"
        UV_PROJECT_ENVIRONMENT=.venv-runtime uv sync --locked --no-default-groups
        if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
        "$(nix-build --no-out-link)" | docker load
      '')
    ];

  shell' =
    with pkgs;
    lib.optionalString isDevelopment ''
      export TZ=UTC
      export NIX_SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt
      export SSL_CERT_FILE=$NIX_SSL_CERT_FILE
      export PYTHONNOUSERSITE=1
      export PYTHONPATH=""

      echo "Installing Python dependencies"
      export UV_PYTHON="${python'}/bin/python"
      uv sync || exit $?

      echo "Activating Python virtual environment"
      source .venv/bin/activate

      if [ -f .env ]; then
        echo "Loading .env file"
        set -o allexport
        source .env set
        set +o allexport
      else
        echo "Skipped loading .env file (not found)"
      fi
    '';
in
pkgs.mkShell {
  buildInputs = packages';
  shellHook = shell';
}
