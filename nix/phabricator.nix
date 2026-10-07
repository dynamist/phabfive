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
  # 0.8.1's Resource has no session, and phabfive's retry mounts an adapter on
  # Resource.session, so every API command crashes before its first request.
  # 0.9.1 adds it, and is what pip installs for the >=0.7.0 floor.
  version = "0.9.1";
  pyproject = true;

  srcHash = "sha256-4VmJSuAaJ/tkAZx0EhC8/Al3X8Qso1uUkXzbv3MaH+0=";
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
