{ pkgs ? import <nixpkgs> { }
, isDocker ? false
}:

with pkgs; let
  commonBuildInputs = [
    stdenv.cc.cc.lib
    python311
    file.out
  ];

  devBuildInputs = [
    gnumake
    gnused
    pipenv
  ];

  commonShellHook = ''
  '';

  devShellHook = ''
    export LD_LIBRARY_PATH="${lib.makeLibraryPath commonBuildInputs}"
    export PIPENV_VENV_IN_PROJECT=1
    export PIPENV_VERBOSITY=-1
    [ ! -f .venv/bin/activate ] && pipenv sync --dev
    case $- in *i*) exec pipenv shell --fancy;; esac
  '';

  dockerShellHook = ''
    make version
  '';
in
pkgs.mkShell {
  buildInputs = commonBuildInputs ++ (if isDocker then [ ] else devBuildInputs);
  shellHook = commonShellHook + (if isDocker then dockerShellHook else devShellHook);
}
