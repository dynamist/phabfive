<?php

/**
 * The admin's SSH public key, so git over SSH works against the seeded
 * repositories.
 *
 * `make deploy` generates the keypair in k8s/base/ssh/ (gitignored), and the
 * public half reaches the pod as the Secret phorge-ssh-key. There is no
 * Conduit method for SSH keys, so like credentials this goes through Phorge's
 * editor, with the transactions PhabricatorAuthSSHKeyEditController applies.
 *
 * Without the file there is nothing to register, which is not an error: an
 * instance deployed some other way simply has no SSH key.
 */
final class PhabfiveSSHKeysSeedModule extends PhabfiveSeedModule {

  const KEY_PATH = '/etc/phorge-seed/ssh/admin.pub';
  const KEY_NAME = 'phabfive-dev';

  public function getKey() {
    return 'sshkeys';
  }

  public function getDependencies() {
    return array('auth');
  }

  public function seed() {
    if (!Filesystem::pathExists(self::KEY_PATH)) {
      $this->log(pht('No %s, no SSH key to add.', self::KEY_PATH));
      return;
    }

    $entire_key = trim(Filesystem::readFile(self::KEY_PATH));
    $public_key = PhabricatorAuthSSHPublicKey::newFromRawKey($entire_key);
    $admin = $this->getActor();

    $existing = id(new PhabricatorAuthSSHKeyQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withObjectPHIDs(array($admin->getPHID()))
      ->withIsActive(true)
      ->execute();
    foreach ($existing as $key) {
      if ($key->getKeyBody() === $public_key->getBody()) {
        return;
      }
    }

    $key = PhabricatorAuthSSHKey::initializeNewSSHKey($admin, $admin);

    $xactions = array();
    $xactions[] = id(new PhabricatorAuthSSHKeyTransaction())
      ->setTransactionType(PhabricatorTransactions::TYPE_CREATE);
    $xactions[] = id(new PhabricatorAuthSSHKeyTransaction())
      ->setTransactionType(PhabricatorAuthSSHKeyTransaction::TYPE_NAME)
      ->setNewValue(self::KEY_NAME);
    $xactions[] = id(new PhabricatorAuthSSHKeyTransaction())
      ->setTransactionType(PhabricatorAuthSSHKeyTransaction::TYPE_KEY)
      ->setNewValue($entire_key);

    $this->applyTransactions(
      new PhabricatorAuthSSHKeyEditor(),
      $key,
      $xactions);

    $this->log(pht(
      'Added SSH key "%s" to %s.',
      self::KEY_NAME,
      $admin->getUsername()));
  }

}
