{ isDevelopment ? true }:

let
  # Currently using nixpkgs-23.11-darwin
  # Update with `nixpkgs-update` command
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/5cd1bc5c4d702f3ccc26fa93f86d05d5684fe5a3.tar.gz") { };

  libraries' = with pkgs; [
    # Base libraries
    stdenv.cc.cc.lib
    file.out
    libxml2.out
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
    (writeShellScriptBin "nixpkgs-update" ''
      set -e
      hash=$(git ls-remote https://github.com/NixOS/nixpkgs nixpkgs-23.11-darwin | cut -f 1)
      sed -i -E "s|/nixpkgs/archive/[0-9a-f]{40}\.tar\.gz|/nixpkgs/archive/$hash.tar.gz|" shell.nix
      echo "Nixpkgs updated to $hash"
    '')
    (writeShellScriptBin "docker-build" ''
      set -e
      cython-clean && cython-build
      if command -v podman &> /dev/null; then docker() { podman "$@"; } fi
      docker load < "$(sudo nix-build --no-out-link)"
    '')
  ];

  shell' = with pkgs; lib.optionalString isDevelopment ''
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
