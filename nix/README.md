# Phabfive Nix packaging

[pydd](https://github.com/nixidae/nixidae/tree/main/pydd) builds phabfive
from `uv.lock`. A planner reads the lock in the build and writes one
derivation per package, so this repository restates no dependency.

The build uses recursive-nix. Nix needs these settings. The `extra-` forms
add to the default lists, which a plain `system-features` replaces:

```
extra-experimental-features = nix-command flakes ca-derivations dynamic-derivations recursive-nix
extra-system-features = recursive-nix
```

`flake.nix` holds the outputs. `default.nix` evaluates the same flake with
[flake-compatish](https://github.com/lillecarl/flake-compatish), which reads
the checkout in place and takes nixpkgs from `NIX_PATH` when it names one:

| flake | without flakes | holds |
| --- | --- | --- |
| `nix build .#phabfive` | `nix build --file . impure.packages.phabfive` | the program, installed from its sdist |
| `nix develop` | `nix develop --file shell.nix` | the `dev` dependency group, with phabfive editable over the checkout |

To build against a local checkout of pydd, name it in `overrides.nix`, which
git ignores:

```nix
{
  nixidae = ~/Code/nixidae;
}
```
