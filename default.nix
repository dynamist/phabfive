{
  # if no pkgs instance is supplied we use NIX_PATH or flake.lock
  pkgs ?
    import
      (
        let
          flake-compatish = import (
            fetchTree (builtins.fromJSON (builtins.readFile ./flake.lock)).nodes.flake-compatish.locked
          );
        in
        flake-compatish {
          source = ./.;
          overrides = {
            # override nixpkgs with NIX_PATH if available
            nixpkgs = <nixpkgs>;
          };
        }
      ).inputs.nixpkgs
      { },
}:
rec {
  # (re)export nixpkgs
  inherit pkgs;
  # development shell allowing 0 effort Python development
  shell = pkgs.callPackage ./nix/shell.nix {
    inherit phabfive;
  };
  phabfive = pkgs.python3Packages.callPackage ./nix/phabfive.nix { inherit phabricator; };
  phabricator = pkgs.python3Packages.callPackage ./nix/phabricator.nix { };
}
