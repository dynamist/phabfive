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
        $user = id(new PhabricatorUser())
          ->setUsername($record['username'])
          ->setRealName($record['realname'])
          ->setIsApproved(1);

        $email = id(new PhabricatorUserEmail())
          ->setAddress($record['email'])
          ->setIsVerified(1);

        id(new PhabricatorUserEditor())
          ->setActor($this->getActor())
          ->createNewUser($user, $email);

        $this->log(pht('Created user "%s".', $record['username']));
      }

      if (phutil_nonempty_string($password)) {
        PhabfiveSeedPasswords::ensure($user, $password);
      }
    }
  }

}
