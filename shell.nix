{ isDevelopment ? true }:

let
  # Currently using nixpkgs-23.11-darwin
  # Get latest hashes from https://status.nixos.org/
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/207b14c6bd1065255e6ecffcfe0c36a7b54f8e48.tar.gz") { };

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
      python "$PROJECT_DIR/setup.py" build_ext --build-lib "$PROJECT_DIR/cython_lib"
    '')
    (writeShellScriptBin "cython-clean" ''
      rm -rf "$PROJECT_DIR/build/" "$PROJECT_DIR/cython_lib/"*{.c,.html,.so}
    '')

    # -- Docker (dev)
    (writeShellScriptBin "dev-start" ''
      docker compose -f docker-compose.dev.yml up -d
    '')
    (writeShellScriptBin "dev-stop" ''
      docker compose -f docker-compose.dev.yml down
    '')
    (writeShellScriptBin "dev-logs" ''
      docker compose -f docker-compose.dev.yml logs -f
    '')
    (writeShellScriptBin "dev-clean" ''
      dev-stop
      rm -rf data/db
    '')

    # -- Misc
    (writeShellScriptBin "docker-build" ''
      set -e
      cython-clean && cython-build

      # Some data files require elevated permissions
      if [ -d "$PROJECT_DIR/data" ]; then
        image_path=$(sudo nix-build --no-out-link)
      else
        image_path=$(nix-build --no-out-link)
      fi

      docker load < "$image_path"
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
  '' + lib.optionalString (!isDevelopment) ''
    make-version
  '';
in
pkgs.mkShell {
  libraries = libraries';
  buildInputs = libraries' ++ packages';
  shellHook = shell';
}
