{
  mkShell,
  python3,
  phabfive,
  ruff,
  pyright,
}:
let
in
mkShell {
  packages = [
    (python3.withPackages (_: [
      phabfive.propagatedBuildInputs
    ]))
    ruff
    pyright
  ];
}
