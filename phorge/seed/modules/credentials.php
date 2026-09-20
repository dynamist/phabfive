<?php

/**
 * Passphrase credentials, in data file order so the K numbers follow it.
 *
 * Passphrase has one Conduit method, `passphrase.query`, and it only reads, so
 * unlike every other module this one goes through Phorge's editors directly.
 * The sequence is the one PassphraseCredentialEditController uses: initialize
 * a credential of the type, let the type fill in a secret if it generates one
 * (`ssh-generated-key` makes a keypair server side), write the plaintext to a
 * PassphraseSecret, then point the credential at it with a transaction.
 *
 * K2 is created with Conduit access off on purpose. Reading its secret has to
 * fail - `passphrase show K2` answers "Access denied" - while attaching it to
 * a repository URI still works, because that needs only the PHID and the type.
 * Between them the five cover what Diffusion asks of a credential: the wrong
 * type (K1, a note), an unreadable secret (K2) and the happy path (K3).
 */
final class PhabfiveCredentialsSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'credentials';
  }

  public function getDependencies() {
    // The admin authors them, and owns them through the default edit policy
    return array('auth');
  }

  public function seed() {
    // No natural key, so the name stands in for one. Destroyed credentials
    // are included: a name is taken until the row is really gone.
    $existing = id(new PassphraseCredentialQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->execute();
    $existing = mpull($existing, null, 'getName');

    foreach ($this->loadData() as $record) {
      if (isset($existing[$record['name']])) {
        continue;
      }

      $credential = $this->createCredential($record);

      $this->log(pht(
        'Created %s "%s" of type "%s"%s.',
        $credential->getMonogram(),
        $record['name'],
        $record['type'],
        $record['conduit'] ? '' : pht(', without Conduit access')));
    }
  }

  private function createCredential(array $record) {
    $actor = $this->getActor();

    $type = PassphraseCredentialType::getTypeByConstant($record['type']);
    if (!$type) {
      throw new Exception(pht(
        'Seed module "%s" asks for credential type "%s", which this instance '.
        'does not have.',
        $this->getKey(),
        $record['type']));
    }

    $credential = PassphraseCredential::initializeNewCredential($actor)
      ->setCredentialType($type->getCredentialType())
      ->setProvidesType($type->getProvidesType())
      ->attachImplementation($type);

    // A type that generates its own material attaches the secret here, which
    // is how an ssh-generated-key gets a keypair without one being pasted in
    $type->didInitializeNewCredential($actor, $credential);

    $secret = idx($record, 'secret');
    if ($secret === null) {
      $secret = $credential->getSecret()->openEnvelope();
    }
    $secret = id(new PassphraseSecret())
      ->setSecretData($secret)
      ->save();

    $xactions = array();
    $xactions[] = $this->newTransaction(
      PassphraseCredentialNameTransaction::TRANSACTIONTYPE,
      $record['name']);
    $xactions[] = $this->newTransaction(
      PassphraseCredentialDescriptionTransaction::TRANSACTIONTYPE,
      $record['description']);
    if ($type->shouldRequireUsername()) {
      $xactions[] = $this->newTransaction(
        PassphraseCredentialUsernameTransaction::TRANSACTIONTYPE,
        $record['username']);
    }
    $xactions[] = $this->newTransaction(
      PassphraseCredentialSecretIDTransaction::TRANSACTIONTYPE,
      $secret->getID());
    $xactions[] = $this->newTransaction(
      PassphraseCredentialConduitTransaction::TRANSACTIONTYPE,
      $record['conduit'] ? 1 : 0);

    $this->applyTransactions(
      new PassphraseCredentialTransactionEditor(),
      $credential,
      $xactions);

    return $credential;
  }

  private function newTransaction($type, $value) {
    return id(new PassphraseCredentialTransaction())
      ->setTransactionType($type)
      ->setNewValue($value);
  }

}
