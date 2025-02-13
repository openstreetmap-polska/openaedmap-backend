{ pkgs ? import <nixpkgs> { } }:

let
  envTag = builtins.getEnv "TAG";

  shell = import ./shell.nix { isDevelopment = false; };

  python-venv = pkgs.buildEnv {
    name = "python-venv";
    paths = [
      (pkgs.runCommand "python-venv" { } ''
        set -e
        mkdir -p $out/bin $out/lib
        find "${./.venv/bin}" -type f -executable -exec cp {} $out/bin \;
        sed -i '1s|^#!.*/python|#!/usr/bin/env python|' $out/bin/*
        cp -r "${./.venv/lib/python3.13/site-packages}"/* $out/lib
      '')
    ];
    pathsToLink = [ "/bin" "/lib" ];
  };

  entrypoint = pkgs.writeShellScriptBin "entrypoint" ''
    set -ex
    rm -f data/postgres/postmaster.pid
    dev-start
    set -o allexport
    source "envs/app/${envTag}.env" set
    set +o allexport
    exec python -m gunicorn main:app "$@"
  '';
in
with pkgs; dockerTools.buildLayeredImage {
  name = "backend";
  tag = if envTag != "" then envTag else "latest";

  contents = shell.buildInputs ++ [
    dockerTools.usrBinEnv
    dockerTools.binSh # initdb dependency
    python-venv
  ];

  extraCommands = ''
    set -e
    mkdir tmp
    mkdir app && cd app
    mkdir -p data/postgres data/photos
    cp "${./.}"/*.py .
    cp -r "${./.}"/alembic_ .
    cp -r "${./.}"/api .
    cp -r "${./.}"/config .
    cp -r "${./.}"/envs .
    cp -r "${./.}"/middlewares .
    cp -r "${./.}"/models .
    cp -r "${./.}"/services .
  '';

  fakeRootCommands = ''
    set -e
    ${dockerTools.shadowSetup}
    chmod 0777 tmp
    groupadd --system -g 999 docker
    useradd --system --no-create-home -u 999 -g 999 docker
    chown -R docker:docker app
  '';

  enableFakechroot = true;

  config = {
    WorkingDir = "/app";
    Env = [
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
      "TZ=UTC"
    ];
    Volumes = {
      "/app/data/postgres" = { };
      "/app/data/photos" = { };
    };
    Ports = [ "8000" ];
    Entrypoint = [ "${entrypoint}/bin/entrypoint" ];
    User = "docker:docker";
  };
}
