{
  # nixpkgs
  lib,
  installShellFiles,
  # python build
  buildPythonApplication,
  hatchling,
  # deps
  anyconfig,
  appdirs,
  inquirerpy,
  jinja2,
  phabricator,
  pyyaml,
  rich,
  ruamel-yaml,
  typer,
}:
buildPythonApplication (finalAttrs: {
  name = "phabfive";
  version = (fromTOML (builtins.readFile ../pyproject.toml)).project.version;
  src = lib.cleanSource ../.;
  pyproject = true;
  build-system = [ hatchling ];
  dependencies = [
    installShellFiles
    # deps
    anyconfig
    appdirs
    inquirerpy
    jinja2
    phabricator
    pyyaml
    rich
    ruamel-yaml
    typer
  ];
  postInstall = # bash
    ''
      # Install shell completions into "well-known" folders (NixOS and home-manager will pick these up)
      installShellCompletion --name phabfive --bash <(env _PHABFIVE_COMPLETE=source_bash $out/bin/phabfive)
      installShellCompletion --name phabfive --fish <(env _PHABFIVE_COMPLETE=source_fish $out/bin/phabfive)
      installShellCompletion --name phabfive --zsh <(env _PHABFIVE_COMPLETE=source_zsh $out/bin/phabfive)
    '';
  meta = {
    homepage = "https://github.com/dynamist/phabfive";
    description = "CLI for Phabricator and Phorge - built for humans and AI agents";
    license = [ lib.licenses.asl20 ];
    mainProgram = "phabfive";
    maintainers = [ lib.maintainers.lillecarl ];
  };
})
