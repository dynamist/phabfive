# this flake doens't look like a normal flake, we're using default.nix to allow
# users to import phabfive with their own nixpkgs instance and to enable evaluating
# without copying to the Nix store while maintaining full flake compatibility.
{
  inputs = {
    flake-compatish = {
      url = "github:lillecarl/flake-compatish";
      flake = false;
    };
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };
  outputs =
    inputs:
    let
      lib = inputs.nixpkgs.lib;
      # attrSet generator generating sets for all supported systems (what NixOS Hydra builds and caches)
      forEachSystem = lib.genAttrs lib.systems.flakeExposed;
      # import default.nix for each supported system with unmodified nixpkgs
      eachDefNix = forEachSystem (system: import ./. { pkgs = inputs.self.legacyPackages.${system}; });
    in
    {
      # expose phabricator and phabfive for each system
      packages = forEachSystem (
        system:
        let
          defNix = eachDefNix.${system};
        in
        rec {
          default = phabfive;
          inherit (defNix) phabfive phabricator;
        }
      );
      # expose devShell for each system
      devShells = forEachSystem (
        system:
        let
          defNix = eachDefNix.${system};
        in
        {
          default = defNix.shell;
        }
      );
      legacyPackages = forEachSystem (system: inputs.nixpkgs.legacyPackages.${system});
      checks = inputs.self.packages;
    };
}
