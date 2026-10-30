<?php

/**
 * One self-contained set of sample data.
 *
 * A module reads its records from data/<key>.json and writes them through
 * Phorge's own editors, so objects get the PHIDs, edges, slugs, search index
 * and transactions the web UI would have given them. seed() must be
 * idempotent: container restarts run every module again.
 */
abstract class PhabfiveSeedModule extends Phobject {

  abstract public function getKey();
  abstract public function seed();

  /**
   * Keys of modules that must have run first.
   */
  public function getDependencies() {
    return array();
  }

  final protected function loadData() {
    $path = dirname(__DIR__).'/data/'.$this->getKey().'.json';
    return phutil_json_decode(Filesystem::readFile($path));
  }

  final protected function log($message) {
    echo tsprintf("  %s\n", $message);
  }

  final protected function env($name, $default = null) {
    $value = getenv($name);
    return ($value === false || $value === '') ? $default : $value;
  }

  final protected function getContentSource() {
    return PhabricatorContentSource::newForSource(
      PhabricatorConsoleContentSource::SOURCECONST);
  }

  /**
   * The account sample data is created as. Before the auth module has run
   * there is none, and the omnipotent user stands in.
   */
  final protected function getActor() {
    $admin = $this->loadUser($this->env('PHORGE_ADMIN_USER', 'admin'));
    return $admin ? $admin : PhabricatorUser::getOmnipotentUser();
  }

  final protected function loadUser($username) {
    return id(new PhabricatorPeopleQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withUsernames(array($username))
      ->needUserSettings(true)
      ->executeOne();
  }

  /**
   * A username a data file refers to, which has to exist by now.
   */
  final protected function requireUser($username) {
    $user = $this->loadUser($username);
    if (!$user) {
      throw new Exception(pht(
        'Seed module "%s" refers to user "%s", who does not exist.',
        $this->getKey(),
        $username));
    }
    return $user;
  }

  /**
   * A permanent command line Conduit token for $user, with the well-known
   * value $secret, so a client can be configured before the instance exists.
   */
  final protected function ensureConduitToken(
    PhabricatorUser $user,
    $secret,
    $name) {

    $token = id(new PhabricatorConduitToken())
      ->loadOneWhere('token = %s', $secret);
    if ($token && $token->getObjectPHID() !== $user->getPHID()) {
      // Two variables with the same value would hand one account's token to
      // whoever was configured with the other
      throw new Exception(pht(
        'Seed module "%s" wants API token "%s" for %s, but it belongs to '.
        'someone else.',
        $this->getKey(),
        $name,
        $user->getUsername()));
    }

    if (!$token) {
      $token = PhabricatorConduitToken::initializeNewToken(
        $user->getPHID(),
        PhabricatorConduitToken::TYPE_COMMANDLINE)
        ->setToken($secret);
      // Tokens have names since July 2026, releases before that have none
      if (property_exists($token, 'tokenName')) {
        $token->setTokenName($name);
      }
      // A "cli" token expires an hour after it is made unless it is used,
      // and the daemons then delete it, so an instance nobody calls in its
      // first hour loses its token. Make it permanent, as using it would
      $token->setExpires(null);
      $token->save();
      $this->log(pht('Created API token for %s.', $user->getUsername()));
    } else if ($token->getExpires()) {
      $token->setExpires(null);
      $token->save();
      $this->log(pht(
        'Made the API token for %s permanent.',
        $user->getUsername()));
    }
  }

  /**
   * Apply transactions as the admin, or as $actor. Acting as someone else is
   * how an object ends up with a policy the admin is not part of: the editor
   * refuses to let its actor lock themselves out.
   */
  final protected function applyTransactions(
    PhabricatorApplicationTransactionEditor $editor,
    $object,
    array $xactions,
    ?PhabricatorUser $actor = null) {

    return $editor
      ->setActor($actor ? $actor : $this->getActor())
      ->setContentSource($this->getContentSource())
      ->setContinueOnNoEffect(true)
      ->setContinueOnMissingFields(true)
      ->applyTransactions($object, $xactions);
  }

  /**
   * Call a Conduit method in-process, for anything with an API. The result is
   * the same shape `phabfive` gets over HTTP.
   */
  final protected function conduit(
    $method,
    array $params,
    ?PhabricatorUser $actor = null) {

    return id(new ConduitCall($method, $params))
      ->setUser($actor ? $actor : $this->getActor())
      ->execute();
  }

  /**
   * A top-level project by name, or a milestone by name under $parent.
   */
  final protected function loadProject($name, $parent = null) {
    $query = id(new PhabricatorProjectQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withNames(array($name));
    if ($parent) {
      $query
        ->withParentProjectPHIDs(array($parent->getPHID()))
        ->withIsMilestone(true);
    } else {
      $query->withDepthBetween(0, 0);
    }
    return $query->executeOne();
  }

  final protected function requireProject($name) {
    $project = $this->loadProject($name);
    if (!$project) {
      throw new Exception(pht(
        'Seed module "%s" refers to project "%s", which does not exist.',
        $this->getKey(),
        $name));
    }
    return $project;
  }

  /**
   * Create a project from a data record: name, and optionally description,
   * icon, color and members (usernames). With $parent it is a milestone,
   * which takes its members from the parent instead.
   */
  final protected function createProject(array $record, $parent = null) {
    $project = PhabricatorProject::initializeNewProject(
      $this->getActor(),
      $parent);

    $xactions = array();
    $xactions[] = $this->newProjectTransaction(
      PhabricatorProjectNameTransaction::TRANSACTIONTYPE,
      $record['name']);

    if ($parent) {
      $xactions[] = $this->newProjectTransaction(
        PhabricatorProjectMilestoneTransaction::TRANSACTIONTYPE,
        $parent->getPHID());
    }

    if (isset($record['description'])) {
      $xactions[] = id(new PhabricatorProjectTransaction())
        ->setTransactionType(PhabricatorTransactions::TYPE_CUSTOMFIELD)
        ->setMetadataValue(
          'customfield:key',
          'std:project:internal:description')
        ->setOldValue('')
        ->setNewValue($record['description']);
    }

    foreach (array('icon', 'color') as $key) {
      if (isset($record[$key])) {
        $xactions[] = $this->newProjectTransaction(
          "project:{$key}",
          $record[$key]);
      }
    }

    $members = array();
    foreach (idx($record, 'members', array()) as $username) {
      $members[] = $this->requireUser($username)->getPHID();
    }
    if ($members && !$parent) {
      $xactions[] = $this->newProjectTransaction(
          PhabricatorTransactions::TYPE_EDGE,
          array('+' => array_fuse($members)))
        ->setMetadataValue(
          'edge:type',
          PhabricatorProjectProjectHasMemberEdgeType::EDGECONST);
    }

    $this->applyTransactions(
      new PhabricatorProjectTransactionEditor(),
      $project,
      $xactions);

    return $project;
  }

  final protected function newProjectTransaction($type, $value) {
    return id(new PhabricatorProjectTransaction())
      ->setTransactionType($type)
      ->setNewValue($value);
  }

}
