{
  # check latest hashes at https://status.nixos.org/
  pkgs = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/4d644e746a64e03cae79645287b1f7d19145f152.tar.gz") { };
  unstable = import (fetchTarball "https://github.com/NixOS/nixpkgs/archive/d6863cbcbbb80e71cecfc03356db1cda38919523.tar.gz") { };
}
