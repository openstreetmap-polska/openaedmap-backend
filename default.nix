{ pkgs ? import <nixpkgs> { } }:

with pkgs; let
  envTag = builtins.getEnv "TAG";

  shell = import ./shell.nix {
    inherit pkgs;
    isDocker = true;
  };

  python-venv = buildEnv {
    name = "python-venv";
    paths = [
      (runCommand "python-venv" { } ''
        mkdir -p $out/lib
        cp -r "${./.venv/lib/python3.11/site-packages}"/* $out/lib
      '')
    ];
  };
in
dockerTools.buildLayeredImage {
  name = "backend";
  tag = if envTag != "" then envTag else "latest";

  contents = shell.buildInputs ++ [ python-venv ];

  extraCommands = ''
    set -e
    mkdir tmp
    mkdir app && cd app
    cp "${./.}"/LICENSE .
    cp "${./.}"/Makefile .
    cp "${./.}"/*.py .
    mkdir -p api/v1 middlewares models states
    cp "${./api/v1}"/*.py api/v1
    cp "${./middlewares}"/*.py middlewares
    cp "${./models}"/*.py models
    cp "${./states}"/*.py states
    ${shell.shellHook}
  '';

  config = {
    WorkingDir = "/app";
    Env = [
      "LD_LIBRARY_PATH=${lib.makeLibraryPath shell.buildInputs}"
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
    ];
    Volumes = {
      "/app/data/photos" = { };
    };
    Entrypoint = [ "python" "-m" "uvicorn" "main:app" ];
    Cmd = [ "--host" "0.0.0.0" ];
  };
}
