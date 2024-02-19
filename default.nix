{ pkgs ? import <nixpkgs> { }, ... }:

let
  envTag = builtins.getEnv "TAG";

  shell = import ./shell.nix {
    isDevelopment = false;
  };

  python-venv = pkgs.buildEnv {
    name = "python-venv";
    paths = [
      (pkgs.runCommand "python-venv" { } ''
        mkdir -p $out/lib
        cp -r "${./.venv/lib/python3.12/site-packages}"/* $out/lib
      '')
    ];
  };
in
with pkgs; dockerTools.buildLayeredImage {
  name = "backend";
  tag = if envTag != "" then envTag else "latest";

  contents = shell.buildInputs ++ [ python-venv ];

  extraCommands = ''
    set -e
    mkdir tmp
    mkdir app && cd app
    cp "${./.}"/*.py .
    cp -r "${./.}"/api .
    cp -r "${./.}"/middlewares .
    cp -r "${./.}"/models .
    cp -r "${./.}"/states .
    export PATH="${lib.makeBinPath shell.buildInputs}:$PATH"
    ${shell.shellHook}
  '';

  config = {
    WorkingDir = "/app";
    Env = [
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
    ];
    Volumes = {
      "/app/data/photos" = { };
    };
    Entrypoint = [ "python" "-m" "uvicorn" "main:app" ];
  };
}
