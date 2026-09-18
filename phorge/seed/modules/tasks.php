<?php

/**
 * Tasks go through maniphest.edit, the same call phabfive makes, so the data
 * file uses the Conduit transaction shape with names in place of PHIDs.
 *
 * A task is filed by the admin unless it names an "author", which a task in a
 * team's Space has to: the admin cannot see that Space to file into it.
 */
final class PhabfiveTasksSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'tasks';
  }

  public function getDependencies() {
    return array('users', 'projects', 'spaces');
  }

  public function seed() {
    $created = 0;

    foreach ($this->loadData() as $record) {
      // No natural key, so the title stands in for one
      $existing = id(new ManiphestTask())
        ->loadOneWhere('title = %s', $record['title']);
      if ($existing) {
        continue;
      }

      $xactions = array(
        array('type' => 'title', 'value' => $record['title']),
      );
      foreach (array('description', 'priority', 'status') as $key) {
        if (isset($record[$key])) {
          $xactions[] = array('type' => $key, 'value' => $record[$key]);
        }
      }
      if (isset($record['owner'])) {
        $xactions[] = array(
          'type' => 'owner',
          'value' => $this->requireUser($record['owner'])->getPHID(),
        );
      }
      if (isset($record['projects'])) {
        $xactions[] = array(
          'type' => 'projects.set',
          'value' => $this->resolveProjects($record['projects']),
        );
      }
      if (isset($record['space'])) {
        $xactions[] = array(
          'type' => 'space',
          'value' => $this->resolveSpace($record['space']),
        );
      }

      $author = isset($record['author'])
        ? $this->requireUser($record['author'])
        : null;

      $this->conduit(
        'maniphest.edit',
        array('transactions' => $xactions),
        $author);
      $created++;
    }

    $this->log(pht('Created %d task(s).', $created));
  }

  private function resolveProjects(array $names) {
    $projects = id(new PhabricatorProjectQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withNames($names)
      ->withDepthBetween(0, 0)
      ->execute();
    return array_values(mpull($projects, 'getPHID'));
  }

  private function resolveSpace($name) {
    $spaces = id(new PhabricatorSpacesNamespaceQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->execute();
    $space = idx(mpull($spaces, null, 'getNamespaceName'), $name);
    if (!$space) {
      throw new Exception(pht('No Space named "%s".', $name));
    }
    return $space->getPHID();
  }

}
