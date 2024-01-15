{ isDevelopment ? true }:

let
  # Currently using nixpkgs-23.11-darwin
  # Get latest hashes from https://status.nixos.org/
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/c327647a296df737bd187bd5fa51a62ee548d5ab.tar.gz") { };

  libraries' = with pkgs; [
    # Base libraries
    stdenv.cc.cc.lib
    file.out
    zlib.out
  ];

  packages' = with pkgs; [
    # Base packages
    python312

    # Scripts
    # -- Misc
    (writeShellScriptBin "make-version" ''
      sed -i -r "s|VERSION = '([0-9.]+)'|VERSION = '\1.$(date +%y%m%d)'|g" config.py
      sed -i -r "s|VERSION_TIMESTAMP = ([0-9.]+)|VERSION_TIMESTAMP = $(date +%s)|g" config.py
    '')
  ] ++ lib.optionals isDevelopment [
    # Development packages
    poetry
    ruff
    gcc

    # Scripts
    # -- Cython
    (writeShellScriptBin "cython-build" ''
      python setup.py build_ext --build-lib cython_lib
    '')
    (writeShellScriptBin "cython-clean" ''
      rm -rf build "cython_lib/"*{.c,.html,.so}
    '')

    # -- Docker (dev)
    (writeShellScriptBin "dev-start" ''
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker compose -f docker-compose.dev.yml up -d
    '')
    (writeShellScriptBin "dev-stop" ''
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker compose -f docker-compose.dev.yml down
    '')
    (writeShellScriptBin "dev-logs" ''
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker compose -f docker-compose.dev.yml logs -f
    '')
    (writeShellScriptBin "dev-clean" ''
      dev-stop
      [ -d data/db ] && sudo rm -r data/db
    '')

    # -- Misc
    (writeShellScriptBin "docker-build" ''
      set -e
      cython-clean && cython-build
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker load < "$(sudo nix-build --no-out-link)"
    '')
  ];

  shell' = with pkgs; ''
    export PROJECT_DIR="$(pwd)"
  '' + lib.optionalString isDevelopment ''
    [ ! -e .venv/bin/python ] && [ -h .venv/bin/python ] && rm -r .venv

    echo "Installing Python dependencies"
    export POETRY_VIRTUALENVS_IN_PROJECT=1
    poetry install --no-root --compile

    echo "Activating Python virtual environment"
    source .venv/bin/activate

    export LD_LIBRARY_PATH="${lib.makeLibraryPath libraries'}"

    # Development environment variables
    if [ -f .env ]; then
      echo "Loading .env file"
      set -o allexport
      source .env set
      +o allexport
    fi
  '' + lib.optionalString (!isDevelopment) ''
    make-version
  '';
in
pkgs.mkShell {
  libraries = libraries';
  buildInputs = libraries' ++ packages';
  shellHook = shell';
}
