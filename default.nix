let
  lock = builtins.fromJSON (builtins.readFile ./flake.lock);
  flake-compatish = builtins.fetchTree lock.nodes.flake-compatish.locked;
in
import flake-compatish {
  source = ./.;
  overrides.nixpkgs = <nixpkgs>;
}
