{
  # nixpkgs
  lib,
  # build
  fetchFromGitHub,
  buildPythonPackage,
  setuptools,
  # deps
  responses,
}:
buildPythonPackage (finalAttrs: {
  name = "phabricator";
  version = "0.8.1";
  pyproject = true;

  srcHash = "sha256-NxP+u4/cNzon9kKaV75ZEp9JiTL0vSOU7VzufF3JI2s=";
  src = fetchFromGitHub {
    owner = "disqus";
    repo = "python-phabricator";
    rev = finalAttrs.version;
    hash = finalAttrs.srcHash;
  };

  # Patch away a setuptools python2 compatibility warning
  patches = [ ./patches/python-phabricator-no-pkg-resources.patch ];

  build-system = [ setuptools ];
  dependencies = [ responses ];

  meta = {
    homepage = "https://github.com/disqus/python-phabricator";
    description = "Python bindings for phabricator";
    license = [ lib.licenses.asl20 ];
    maintainers = [ lib.maintainers.lillecarl ];
  };
})
