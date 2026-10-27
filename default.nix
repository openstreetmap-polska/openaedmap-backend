{
  pkgs ? import ./nixpkgs.nix,
}:

let
  envTag = builtins.getEnv "TAG";
  environmentRelease = builtins.getEnv "SENTRY_RELEASE";
  gitSource = builtins.fetchGit ./.;
  release =
    if environmentRelease != "" then
      environmentRelease
    else if gitSource ? dirtyRev then
      gitSource.dirtyRev
    else
      gitSource.rev;

  rootPython = builtins.map (name: ./. + "/${name}") (
    builtins.filter (name: pkgs.lib.hasSuffix ".py" name) (builtins.attrNames (builtins.readDir ./.))
  );
  appSource = pkgs.lib.fileset.toSource {
    root = ./.;
    fileset = pkgs.lib.fileset.unions (
      rootPython
      ++ [
        ./schema.sql
        ./sql
        ./config
        ./envs/app
      ]
      ++ builtins.map (directory: pkgs.lib.fileset.fileFilter (file: file.hasExt "py") directory) [
        ./api
        ./middlewares
        ./models
        ./services
      ]
    );
  };

  shell = import ./shell.nix { isDevelopment = false; };

  python-venv = pkgs.buildEnv {
    name = "python-venv";
    paths = [
      (pkgs.runCommand "python-venv" { } ''
        set -e
        mkdir -p $out/bin $out/lib
        find "${./.venv-runtime/bin}" -type f -executable -exec cp {} $out/bin \;
        sed -i '1s|^#!.*/python|#!/usr/bin/env python|' $out/bin/*
        cp -r "${./.venv-runtime/lib/python3.15/site-packages}"/* $out/lib
      '')
    ];
    pathsToLink = [
      "/bin"
      "/lib"
    ];
  };

  entrypoint = pkgs.writeShellScriptBin "entrypoint" ''
    set -ex
    # PID files survive a restart; the container's process namespace does not.
    rm -f data/postgres/postmaster.pid data/supervisor/supervisord.pid
    dev-start
    set -o allexport
    ${pkgs.lib.optionalString (envTag != "") ''source "envs/app/${envTag}.env" set''}
    set +o allexport
    exec h2corn main:app "$@"
  '';
in
with pkgs;
dockerTools.streamLayeredImage {
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
    cp "${appSource}"/*.py .
    cp "${appSource}"/schema.sql .
    cp -r "${appSource}"/sql .
    cp -r "${appSource}"/api .
    cp -r "${appSource}"/config .
    cp -r "${appSource}"/envs .
    cp -r "${appSource}"/middlewares .
    cp -r "${appSource}"/models .
    cp -r "${appSource}"/services .
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
    Labels."org.opencontainers.image.revision" = release;
    Env = [
      "TZ=UTC"
      "PYTHONPATH=${python-venv}/lib"
      "PYTHONUNBUFFERED=1"
      "PYTHONDONTWRITEBYTECODE=1"
      "SSL_CERT_FILE=${cacert}/etc/ssl/certs/ca-bundle.crt"
      "SENTRY_RELEASE=${release}"
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
