<?php

/**
 * Spaces, created in data file order so the S numbers follow it.
 *
 * A Space with a "team" can be seen and edited only by that team's members.
 * phid.lookup hides a Space from a viewer without access exactly as though it
 * were absent, so interleaving team Spaces gives the admin sparse visible
 * numbers, which is what anything discovering Spaces has to cope with on a
 * real instance.
 *
 * The editor refuses to let its actor set a policy that excludes themselves,
 * so a team's Space is created by the first member of that team.
 */
final class PhabfiveSpacesSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'spaces';
  }

  public function getDependencies() {
    return array('auth', 'teams');
  }

  public function seed() {
    $existing = id(new PhabricatorSpacesNamespaceQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->execute();
    $existing = mpull($existing, null, 'getNamespaceName');

    foreach ($this->loadData() as $record) {
      $space = idx($existing, $record['name']);
      $team = idx($record, 'team');

      if (!$space) {
        $space = $this->createSpace($record);
        $this->log(pht(
          'Created S%d "%s"%s.',
          $space->getID(),
          $record['name'],
          $team ? pht(', for %s only', $team) : ''));
      }
    }
  }

  private function createSpace(array $record) {
    $actor = null;
    $view = PhabricatorPolicies::POLICY_USER;
    $edit = PhabricatorPolicies::POLICY_ADMIN;

    if (isset($record['team'])) {
      $team = $this->requireProject($record['team']);
      $members = PhabricatorEdgeQuery::loadDestinationPHIDs(
        $team->getPHID(),
        PhabricatorProjectProjectHasMemberEdgeType::EDGECONST);
      $actor = id(new PhabricatorPeopleQuery())
        ->setViewer(PhabricatorUser::getOmnipotentUser())
        ->withPHIDs(array(head($members)))
        ->needUserSettings(true)
        ->executeOne();
      // A project PHID as a policy means "members of that project"
      $view = $team->getPHID();
      $edit = $team->getPHID();
    }

    $space = PhabricatorSpacesNamespace::initializeNewNamespace(
      $actor ? $actor : $this->getActor());

    $xactions = array();
    $xactions[] = $this->newTransaction(
      PhabricatorSpacesNamespaceNameTransaction::TRANSACTIONTYPE,
      $record['name']);
    $xactions[] = $this->newTransaction(
      PhabricatorTransactions::TYPE_VIEW_POLICY,
      $view);
    $xactions[] = $this->newTransaction(
      PhabricatorTransactions::TYPE_EDIT_POLICY,
      $edit);
    if (idx($record, 'default')) {
      $xactions[] = $this->newTransaction(
        PhabricatorSpacesNamespaceDefaultTransaction::TRANSACTIONTYPE,
        true);
    }

    $this->applyTransactions(
      new PhabricatorSpacesNamespaceEditor(),
      $space,
      $xactions,
      $actor);

    return $space;
  }

  private function newTransaction($type, $value) {
    return id(new PhabricatorSpacesNamespaceTransaction())
      ->setTransactionType($type)
      ->setNewValue($value);
  }

}
