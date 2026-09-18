#!/usr/bin/env php
<?php

// Seed a Phorge instance with phabfive's sample data.
//
//   seed.php                 run every module
//   seed.php users projects  run these (and what they depend on)
//   seed.php --list          show the modules

$phorge = getenv('PHORGE_PATH') ? getenv('PHORGE_PATH') : '/app/phorge';
require_once $phorge.'/scripts/init/init-script.php';
require_once __DIR__.'/src/PhabfiveSeedModule.php';

$declared = get_declared_classes();
$files = glob(__DIR__.'/modules/*.php');
sort($files);
foreach ($files as $file) {
  require_once $file;
}

$modules = array();
foreach (array_diff(get_declared_classes(), $declared) as $class) {
  $reflection = new ReflectionClass($class);
  if ($reflection->isSubclassOf('PhabfiveSeedModule') &&
      !$reflection->isAbstract()) {
    $module = new $class();
    $modules[$module->getKey()] = $module;
  }
}

$args = array_slice($argv, 1);

if (in_array('--list', $args)) {
  foreach ($modules as $key => $module) {
    $deps = $module->getDependencies();
    echo $key.($deps ? ' (needs '.implode(', ', $deps).')' : '')."\n";
  }
  exit(0);
}

// Resolve the selection, pulling in dependencies depth first so each module
// runs after everything it needs
$selected = $args ? $args : array_keys($modules);
$order = array();
$visit = function ($key, array $path) use (&$visit, &$order, $modules) {
  if (!isset($modules[$key])) {
    throw new Exception(pht('No seed module "%s".', $key));
  }
  if (in_array($key, $path)) {
    throw new Exception(pht('Dependency cycle: %s.', implode(' > ', $path)));
  }
  foreach ($modules[$key]->getDependencies() as $dependency) {
    $visit($dependency, array_merge($path, array($key)));
  }
  $order[$key] = $modules[$key];
};
foreach ($selected as $key) {
  $visit($key, array());
}

foreach ($order as $key => $module) {
  // Phorge caches lookups for the length of a request, and a whole seed run
  // is one request. Without this, whatever the Spaces module created stays
  // invisible to the modules after it, and their objects land in no Space.
  PhabricatorCaches::getRequestCache()->destroyCache();

  echo tsprintf("**<bg:blue> SEED </bg>** %s\n", $key);
  $module->seed();
}
