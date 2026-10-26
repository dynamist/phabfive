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

      foreach (idx($record, 'hidden_columns', array()) as $name) {
        $this->ensureHiddenColumn($project, $name);
      }

      $milestones = array();
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
        $milestones[] = $milestone;
      }

      $this->ensureProxyColumns($project, $milestones);
    }
  }

  /**
   * Phorge stores no column of a workboard as hidden until someone hides one
   * from the board, so the seed hides one itself, at the end of the board.
   */
  private function ensureHiddenColumn(PhabricatorProject $project, $name) {
    $columns = $this->loadColumns($project);
    foreach ($columns as $column) {
      if ($column->getName() === $name) {
        return;
      }
    }

    PhabricatorProjectColumn::initializeNewColumn($this->getActor())
      ->setProjectPHID($project->getPHID())
      ->setSequence($this->nextSequence($columns))
      ->setName($name)
      ->setStatus(PhabricatorProjectColumn::STATUS_HIDDEN)
      ->save();

    $this->log(pht('Hid column "%s" in "%s".', $name, $project->getName()));
  }

  /**
   * A milestone's column on its parent's board. Phorge creates it the first
   * time the board is drawn (PhabricatorBoardLayoutEngine), which a seeded
   * instance nobody has opened yet never does, so project.column.search
   * would not list it. This creates it the same way.
   */
  private function ensureProxyColumns(PhabricatorProject $project, array $children) {
    $columns = $this->loadColumns($project);
    $proxied = mpull($columns, null, 'getProxyPHID');

    foreach ($children as $child) {
      if (isset($proxied[$child->getPHID()])) {
        continue;
      }

      $column = PhabricatorProjectColumn::initializeNewColumn($this->getActor())
        ->setProjectPHID($project->getPHID())
        ->setProxyPHID($child->getPHID())
        ->setSequence($this->nextSequence($columns))
        ->save();
      $columns[] = $column;

      $this->log(pht(
        'Added the column of "%s" to "%s".',
        $child->getName(),
        $project->getName()));
    }
  }

  private function loadColumns(PhabricatorProject $project) {
    return id(new PhabricatorProjectColumnQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withProjectPHIDs(array($project->getPHID()))
      ->execute();
  }

  private function nextSequence(array $columns) {
    $sequences = mpull($columns, 'getSequence');
    return $sequences ? max($sequences) + 1 : 0;
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
