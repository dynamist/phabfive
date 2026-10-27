<?php

/**
 * Tasks created and closed at known dates, for `maniphest stats` (#598).
 *
 * Conduit cannot backdate anything, so each task is created and closed through
 * maniphest.edit like any other, and then its dateCreated and closedEpoch are
 * written straight to the row. Those are the columns maniphest.search's
 * createdStart/createdEnd and closedStart/closedEnd compare against, so the
 * search sees the dates as if the work had happened then. Every date is noon
 * UTC on a Wednesday, which falls on the same day and ISO week in every
 * timezone from UTC-11 to UTC+11.
 *
 * Idempotent: a task is found by its title, and the dates are written again on
 * every run, which changes nothing the second time.
 */
final class PhabfiveStatsSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'stats';
  }

  public function getDependencies() {
    return array('users', 'projects');
  }

  public function seed() {
    $data = $this->loadData();

    $project = $this->loadProject($data['project']['name']);
    if (!$project) {
      $project = $this->createProject($data['project']);
    }

    $created = 0;
    foreach ($data['tasks'] as $record) {
      $task = id(new ManiphestTask())
        ->loadOneWhere('title = %s', $record['title']);
      if (!$task) {
        $this->createTask($record, $project);
        $task = id(new ManiphestTask())
          ->loadOneWhere('title = %s', $record['title']);
        $created++;
      }
      $this->backdate($task, $record);
    }

    $this->log(pht('Created %d task(s) at known dates.', $created));
  }

  private function createTask(array $record, PhabricatorProject $project) {
    $xactions = array(
      array('type' => 'title', 'value' => $record['title']),
      array('type' => 'projects.set', 'value' => array($project->getPHID())),
    );
    if (isset($record['owner'])) {
      $xactions[] = array(
        'type' => 'owner',
        'value' => $this->requireUser($record['owner'])->getPHID(),
      );
    }

    $result = $this->conduit(
      'maniphest.edit',
      array('transactions' => $xactions));
    $phid = $result['object']['phid'];

    // Closed in an edit of its own, so it has a close to backdate
    $status = idx($record, 'status');
    if (idx($record, 'reopened')) {
      $status = 'resolved';
    }
    if ($status) {
      $this->conduit('maniphest.edit', array(
        'objectIdentifier' => $phid,
        'transactions' => array(array('type' => 'status', 'value' => $status)),
      ));
    }
    if (idx($record, 'reopened')) {
      $this->conduit('maniphest.edit', array(
        'objectIdentifier' => $phid,
        'transactions' => array(array('type' => 'status', 'value' => 'open')),
      ));
    }
  }

  private function backdate(ManiphestTask $task, array $record) {
    $conn = $task->establishConnection('w');

    queryfx(
      $conn,
      'UPDATE %T SET dateCreated = %d WHERE id = %d',
      $task->getTableName(),
      $this->epoch($record['created']),
      $task->getID());

    if (isset($record['closed'])) {
      queryfx(
        $conn,
        'UPDATE %T SET closedEpoch = %d WHERE id = %d',
        $task->getTableName(),
        $this->epoch($record['closed']),
        $task->getID());
    }
  }

  private function epoch($text) {
    $epoch = strtotime($text);
    if ($epoch === false) {
      throw new Exception(pht('Not a date: "%s".', $text));
    }
    return $epoch;
  }

}
