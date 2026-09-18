<?php

/**
 * What the instance needs to be usable at all: password login, the admin
 * account and its API token. Configured from the PHORGE_ADMIN_* variables
 * rather than a data file, since k8s/base/config.env already sets those.
 */
final class PhabfiveAuthSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'auth';
  }

  public function seed() {
    $this->seedPasswordProvider();

    $username = $this->env('PHORGE_ADMIN_USER', 'admin');
    $admin = $this->loadUser($username);
    if ($admin) {
      $this->log(pht('Admin "%s" exists.', $username));
    } else {
      $admin = id(new PhabricatorUser())
        ->setUsername($username)
        ->setRealName($this->env('PHORGE_ADMIN_NAME', 'Administrator'))
        ->setIsApproved(1)
        ->setIsAdmin(1);

      $email = id(new PhabricatorUserEmail())
        ->setAddress($this->env('PHORGE_ADMIN_EMAIL', 'admin@domain.tld'))
        ->setIsVerified(1);

      id(new PhabricatorUserEditor())
        ->setActor(PhabricatorUser::getOmnipotentUser())
        ->createNewUser($admin, $email);

      $this->log(pht('Created admin "%s".', $username));
    }

    $password = $this->env('PHORGE_ADMIN_PASS');
    if (phutil_nonempty_string($password)) {
      PhabfiveSeedPasswords::ensure($admin, $password);
    }

    $secret = $this->env(
      'PHORGE_ADMIN_TOKEN',
      'api-supersecr3tapikeyfordevelop1');
    $token = id(new PhabricatorConduitToken())
      ->loadOneWhere('token = %s', $secret);
    if (!$token) {
      PhabricatorConduitToken::initializeNewToken(
        $admin->getPHID(),
        PhabricatorConduitToken::TYPE_COMMANDLINE)
        ->setTokenName('phabfive-dev')
        ->setToken($secret)
        ->save();
      $this->log(pht('Created API token.'));
    }
  }

  private function seedPasswordProvider() {
    $provider = new PhabricatorPasswordAuthProvider();

    $existing = id(new PhabricatorAuthProviderConfig())->loadOneWhere(
      'providerClass = %s',
      get_class($provider));
    if ($existing) {
      return;
    }

    $provider->getDefaultProviderConfig()
      ->setProviderType($provider->getProviderType())
      ->setProviderDomain($provider->getProviderDomain())
      ->save();

    $this->log(pht('Enabled username/password login.'));
  }

}

/**
 * Shared by every module that makes accounts someone can log in to.
 */
final class PhabfiveSeedPasswords extends Phobject {

  public static function ensure(PhabricatorUser $user, $password) {
    $type = PhabricatorAuthPassword::PASSWORD_TYPE_ACCOUNT;

    $existing = id(new PhabricatorAuthPasswordQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withObjectPHIDs(array($user->getPHID()))
      ->withPasswordTypes(array($type))
      ->withIsRevoked(false)
      ->execute();
    if ($existing) {
      return;
    }

    PhabricatorAuthPassword::initializeNewPassword($user, $type)
      ->setPassword(new PhutilOpaqueEnvelope($password), $user)
      ->save();
  }

}
