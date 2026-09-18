<?php

/**
 * Teams are projects used as groups, with no workboard. A Space restricted to
 * a team the admin is not in is one the dev API token cannot see, which is
 * the point of most of them. A team with "admin": true has the admin as a
 * member, for a restricted Space the token can see.
 */
final class PhabfiveTeamsSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'teams';
  }

  public function getDependencies() {
    return array('users');
  }

  public function seed() {
    foreach ($this->loadData() as $record) {
      // By flag rather than by username, so PHORGE_ADMIN_USER is honoured
      if (idx($record, 'admin')) {
        $record['members'] = array_merge(
          array($this->env('PHORGE_ADMIN_USER', 'admin')),
          idx($record, 'members', array()));
      }

      if (!$this->loadProject($record['name'])) {
        if (!idx($record, 'members')) {
          throw new Exception(pht(
            'Team "%s" needs a member, or nobody could create or see its '.
            'Space.',
            $record['name']));
        }

        $this->createProject($record + array('icon' => 'group'));
        $this->log(pht(
          'Created "%s" with %s.',
          $record['name'],
          implode(', ', $record['members'])));
      }
    }
  }

}
