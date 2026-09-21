<?php

final class PhabfiveUsersSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'users';
  }

  public function getDependencies() {
    return array('auth');
  }

  public function seed() {
    $password = $this->env('PHORGE_ADMIN_PASS');

    foreach ($this->loadData() as $record) {
      $user = $this->loadUser($record['username']);

      if (!$user) {
        // A bot and a disabled account, so that the roles user.search
        // reports on them - "bot", "disabled" - exist to be filtered on.
        $user = id(new PhabricatorUser())
          ->setUsername($record['username'])
          ->setRealName($record['realname'])
          ->setIsApproved(1)
          ->setIsSystemAgent((int)!empty($record['bot']))
          ->setIsDisabled((int)!empty($record['disabled']));

        $email = id(new PhabricatorUserEmail())
          ->setAddress($record['email'])
          ->setIsVerified(1);

        id(new PhabricatorUserEditor())
          ->setActor($this->getActor())
          ->createNewUser($user, $email);

        $this->log(pht('Created user "%s".', $record['username']));
      }

      // Neither a bot nor a disabled account logs in with a password
      $can_log_in = empty($record['bot']) && empty($record['disabled']);

      if ($can_log_in && phutil_nonempty_string($password)) {
        PhabfiveSeedPasswords::ensure($user, $password);
      }
    }
  }

}
