{
  inputs = {
    flake-compatish = {
      url = "github:lillecarl/flake-compatish";
      flake = false;
    };
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
    nixidae = {
      url = "github:nixidae/nixidae";
      flake = false;
    };
  };
  outputs =
    inputs:
    let
      lib = inputs.nixpkgs.lib;
      forEachSystem = lib.genAttrs lib.systems.flakeExposed;
      # The sdist build reads the source tree, so name what it reads: ./.
      # would bring .venv
      metadata = [
        ./pyproject.toml
        ./uv.lock
        ./README.md
        ./LICENSE
      ];
      project = lib.fileset.toSource {
        root = ./.;
        fileset = lib.fileset.unions (
          metadata
          # Running from the checkout rewrites the bytecode, which would rebuild
          ++ [
            (lib.fileset.difference ./phabfive (lib.fileset.fileFilter (file: file.hasExt "pyc") ./phabfive))
          ]
        );
      };
      # The shell installs phabfive editable, so an edit of the code must not
      # change its plan
      shellProject = lib.fileset.toSource {
        root = ./.;
        # hatchling maps the editable only to a package it finds
        fileset = lib.fileset.unions (metadata ++ [ ./phabfive/__init__.py ]);
      };
      perSystem =
        system:
        let
          pkgs = inputs.self.legacyPackages.${system};
          # Recursive mode on released Nix: the planner writes every node
          # through the daemon socket that recursive-nix gives the build
          pydd = (import inputs.nixidae { inherit system; }).pydd.lib.recursive;
        in
        {
          phabfive =
            (pydd.python.uv {
              inherit project;
              fromSdist = [ "phabfive" ];
              groups = [ ];
              application = "phabfive";
            }).overrideAttrs
              (
                finalAttrs: old: {
                  nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ [ pkgs.installShellFiles ];
                  postInstall = (old.postInstall or "") + ''
                    installShellCompletion --cmd phabfive \
                      --bash <(_PHABFIVE_COMPLETE=source_bash $out/bin/phabfive) \
                      --fish <(_PHABFIVE_COMPLETE=source_fish $out/bin/phabfive) \
                      --zsh <(_PHABFIVE_COMPLETE=source_zsh $out/bin/phabfive)

                    # The share/skills layout an enumerator globs for (nixpkgs#547426)
                    mkdir -p $out/share/skills/phabfive/phabfive
                    $out/bin/phabfive --skill > $out/share/skills/phabfive/phabfive/SKILL.md
                  '';
                  # A config writes pkgs.phabfive.skill and needs no knowledge of the layout
                  passthru = old.passthru // {
                    skill = "${finalAttrs.finalPackage}/share/skills/phabfive/phabfive/SKILL.md";
                  };
                  meta = old.meta // {
                    homepage = "https://github.com/dynamist/phabfive";
                    description = "CLI for Phabricator and Phorge - built for humans and AI agents";
                    license = lib.licenses.asl20;
                    maintainers = [ lib.maintainers.lillecarl ];
                  };
                }
              );
          # The dev group from uv.lock, with phabfive editable over this checkout.
          # Pure evaluation has no currentSystem, and ./. is then a store path,
          # so the shell finds the checkout when it starts instead.
          shell = (pydd.python.uv { project = shellProject; }).shell.overrideAttrs (
            if builtins ? currentSystem then
              { PYDD_EDITABLE_ROOT = toString ./.; }
            else
              {
                PYDD_EDITABLE_ROOT = null;
                shellHook = ''
                  export PYDD_EDITABLE_ROOT="$(${lib.getExe pkgs.git} rev-parse --show-toplevel 2>/dev/null || pwd)"
                '';
              }
          );
        };
      eachSystem = forEachSystem perSystem;
    in
    {
      packages = forEachSystem (system: {
        inherit (eachSystem.${system}) phabfive;
        default = eachSystem.${system}.phabfive;
      });
      devShells = forEachSystem (system: {
        default = eachSystem.${system}.shell;
      });
      legacyPackages = forEachSystem (system: inputs.nixpkgs.legacyPackages.${system});
      checks = inputs.self.packages;
    };
}
