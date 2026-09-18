<?php

final class PhabfiveProjectsSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'projects';
  }

  public function getDependencies() {
    // Spaces first, so projects land in the default Space as they would on
    // an instance that had Spaces before anyone made a project
    return array('users', 'spaces');
  }

  public function seed() {
    $data = $this->loadData();
    $admin = $this->env('PHORGE_ADMIN_USER', 'admin');

    foreach ($data['projects'] as $record) {
      $project = $this->loadProject($record['name']);
      if (!$project) {
        // The admin works in every project
        $record['members'] = array_merge(
          array($admin),
          idx($record, 'members', array()));
        $project = $this->createProject($record);
        $this->createWorkboard($project, $data['columns']);
        $this->log(pht('Created "%s".', $record['name']));
      }

      foreach (idx($record, 'milestones', array()) as $milestone_record) {
        $milestone = $this->loadProject($milestone_record['name'], $project);
        if (!$milestone) {
          $milestone = $this->createProject($milestone_record, $project);
          $this->createWorkboard($milestone, $data['columns']);
          $this->log(pht(
            'Created "%s" in "%s".',
            $milestone_record['name'],
            $record['name']));
        }
      }
    }
  }

  private function createWorkboard(PhabricatorProject $project, array $columns) {
    $actor = $this->getActor();

    // Sequence 0 is the default column, shown as "Backlog"
    $names = array_merge(array(''), $columns);
    foreach ($names as $sequence => $name) {
      PhabricatorProjectColumn::initializeNewColumn($actor)
        ->setProjectPHID($project->getPHID())
        ->setSequence($sequence)
        ->setName($name)
        ->setProperty('isDefault', ($sequence == 0))
        ->save();
    }

    $this->applyTransactions(
      new PhabricatorProjectTransactionEditor(),
      $project,
      array(
        $this->newProjectTransaction(
          PhabricatorProjectWorkboardTransaction::TRANSACTIONTYPE,
          1),
      ));

    // Open the workboard rather than the profile at /tag/<slug>/
    PhabricatorProfileMenuItemConfiguration::initializeNewBuiltin()
      ->setProfilePHID($project->getPHID())
      ->setBuiltinKey(PhabricatorProject::ITEM_WORKBOARD)
      ->setMenuItemKey(PhabricatorProjectWorkboardProfileMenuItem::MENUITEMKEY)
      ->setVisibility(PhabricatorProfileMenuItemConfiguration::VISIBILITY_DEFAULT)
      ->setMenuItemProperties(array())
      ->save();
  }

}
