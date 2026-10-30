<?php

/**
 * The account Dynatron's standup page calls Conduit as, so it does not have
 * to hold the admin's token.
 *
 * A bot and nothing more: no admin, no password, no project memberships. It
 * sees what any logged-in user sees, which is enough for user.whoami and the
 * searches the standup page makes. The token is a well-known development
 * value, PHORGE_DYNATRON_TOKEN, like the admin's.
 */
final class PhabfiveDynatronSeedModule extends PhabfiveSeedModule {

  const USERNAME = 'dynatron';

  public function getKey() {
    return 'dynatron';
  }

  public function getDependencies() {
    return array('auth');
  }

  public function seed() {
    $bot = $this->loadUser(self::USERNAME);
    if (!$bot) {
      $bot = id(new PhabricatorUser())
        ->setUsername(self::USERNAME)
        ->setRealName('Dynatron')
        ->setIsApproved(1)
        ->setIsSystemAgent(1);

      $email = id(new PhabricatorUserEmail())
        ->setAddress('dynatron@domain.tld')
        ->setIsVerified(1);

      id(new PhabricatorUserEditor())
        ->setActor($this->getActor())
        ->createNewUser($bot, $email);

      $this->log(pht('Created bot "%s".', self::USERNAME));
    }

    $this->ensureConduitToken(
      $bot,
      $this->env('PHORGE_DYNATRON_TOKEN', 'api-dynatr0nb0tapikeyfordevelop1'),
      'dynatron');
  }

}
