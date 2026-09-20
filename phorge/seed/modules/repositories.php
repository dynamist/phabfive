<?php

/**
 * Hosted git repositories, with the history a ref query needs.
 *
 * Hosted rather than observed: Phorge hosts any repository that is given no
 * "Observe" URI, and a hosted repository is never fetched from anywhere, so
 * none of this needs the network.
 *
 * The repository row goes through `diffusion.repository.edit`, the same call
 * phabfive makes. Its working copy does not: on a running instance that is
 * PhabricatorRepositoryPullLocalDaemon's job, done whenever the daemon next
 * looks, and a test that wants a branch would have to wait out an import with
 * no defined end. The seeder runs before the daemons start, so it does that
 * work itself, synchronously - the same PhabricatorRepositoryPullEngine the
 * daemon uses - and writes the history in before anything can read it. By the
 * time Apache serves the first request, `git for-each-ref` already answers,
 * which is all diffusion.branchquery and diffusion.tagsquery ask of it. The
 * daemons still import the commits afterwards; nothing waits for them.
 *
 * The history is built in a scratch working copy and fetched into the bare
 * repository, rather than pushed: a hosted repository has Phorge's commit
 * hooks installed, and a fetch does not run them. Every commit has a fixed
 * author, committer and date in the data file, so a repository seeded twice
 * has the same commit hashes both times.
 *
 * Anything this writes ends up owned by the user that serves Conduit. The
 * seeder runs as root, and git refuses to read a repository owned by another
 * user ("detected dubious ownership"), which is what broke every ref query
 * in #369.
 */
final class PhabfiveRepositoriesSeedModule extends PhabfiveSeedModule {

  public function getKey() {
    return 'repositories';
  }

  public function getDependencies() {
    // The admin creates them, and the commit author is a seeded user, which
    // is who the daemons attribute the commits to once they import them
    return array('auth', 'users');
  }

  public function seed() {
    $data = $this->loadData();

    foreach ($data['repositories'] as $record) {
      $repository = $this->loadRepository($record['callsign']);
      if (!$repository) {
        $repository = $this->createRepository($record);
        $this->log(pht(
          'Created %s "%s".',
          $repository->getMonogram(),
          $record['name']));
      }

      if ($this->createWorkingCopy($repository, $record, $data['author'])) {
        $this->log(pht(
          'Initialized the working copy of %s%s.',
          $repository->getMonogram(),
          idx($record, 'branches')
            ? pht(' with %d branch(es)', count($record['branches']))
            : pht(', which is empty')));
      }
    }
  }

  private function loadRepository($callsign) {
    return id(new PhabricatorRepositoryQuery())
      ->setViewer(PhabricatorUser::getOmnipotentUser())
      ->withCallsigns(array($callsign))
      ->executeOne();
  }

  private function createRepository(array $record) {
    $xactions = array(
      array('type' => 'vcs', 'value' => 'git'),
      array('type' => 'name', 'value' => $record['name']),
      array('type' => 'callsign', 'value' => $record['callsign']),
      array('type' => 'shortName', 'value' => $record['shortName']),
      array('type' => 'description', 'value' => $record['description']),
      // A new repository is inactive until it is activated
      array('type' => 'status', 'value' => 'active'),
    );
    if (isset($record['defaultBranch'])) {
      $xactions[] = array(
        'type' => 'defaultBranch',
        'value' => $record['defaultBranch'],
      );
    }

    $this->conduit(
      'diffusion.repository.edit',
      array('transactions' => $xactions));

    // Reload it: the editor assigns the local path once the row has an ID
    $repository = $this->loadRepository($record['callsign']);
    if (!$repository) {
      throw new Exception(pht(
        'Seed module "%s" created repository "%s", which cannot be loaded '.
        'back.',
        $this->getKey(),
        $record['callsign']));
    }

    return $repository;
  }

  /**
   * Create the on-disk repository and write its history, if it is not there
   * already. True when it did something.
   */
  private function createWorkingCopy(
    PhabricatorRepository $repository,
    array $record,
    array $author) {

    $path = rtrim($repository->getLocalPath(), '/');
    if (Filesystem::pathExists($path)) {
      return false;
    }

    // What the pull daemon would do on its next pass: "git init --bare", the
    // commit hooks, and the working copy configuration
    id(new PhabricatorRepositoryPullEngine())
      ->setRepository($repository)
      ->pullRepository();

    $branches = idx($record, 'branches', array());
    if ($branches) {
      $this->writeHistory($path, $branches, $author);
      if (isset($record['defaultBranch'])) {
        $this->git($path, array(
          'git symbolic-ref HEAD %s',
          'refs/heads/'.$record['defaultBranch'],
        ));
      }
    }

    $this->grantWorkingCopyOwnership($path);

    return true;
  }

  /**
   * Build the history in a scratch working copy and fetch it into the bare
   * repository at $path.
   */
  private function writeHistory($path, array $branches, array $author) {
    $identity = array(
      'GIT_AUTHOR_NAME' => $author['name'],
      'GIT_AUTHOR_EMAIL' => $author['email'],
      'GIT_COMMITTER_NAME' => $author['name'],
      'GIT_COMMITTER_EMAIL' => $author['email'],
    );

    $first = head($branches);

    $scratch = Filesystem::createTemporaryDirectory('phabfive-seed');
    try {
      $this->git($scratch, array(
        'git init --quiet --initial-branch=%s',
        $first['name'],
      ));

      foreach ($branches as $index => $branch) {
        if ($index) {
          $this->git($scratch, array(
            'git checkout --quiet -b %s %s',
            $branch['name'],
            $branch['from'],
          ));
        }

        foreach ($branch['commits'] as $commit) {
          foreach ($commit['files'] as $file => $contents) {
            $full = $scratch.'/'.$file;
            Filesystem::createDirectory(dirname($full), 0755, true);
            Filesystem::writeFile($full, $contents);
          }

          $this->git($scratch, array('git add --all'));
          $this->git(
            $scratch,
            array('git commit --quiet --message %s', $commit['message']),
            $identity + array(
              // A fixed date, so the same history hashes the same every time
              'GIT_AUTHOR_DATE' => $commit['date'],
              'GIT_COMMITTER_DATE' => $commit['date'],
            ));

          if (isset($commit['tag'])) {
            $this->git($scratch, array('git tag -- %s', $commit['tag']));
          }
        }
      }

      // A fetch rather than a push: a hosted repository has Phorge's commit
      // hooks installed, and they reject a push that did not come through
      // Phorge
      $this->git($path, array(
        'git fetch --quiet -- %s %s %s',
        $scratch,
        '+refs/heads/*:refs/heads/*',
        '+refs/tags/*:refs/tags/*',
      ));
    } catch (Exception $ex) {
      Filesystem::remove($scratch);
      throw $ex;
    }

    Filesystem::remove($scratch);
  }

  /**
   * Hand the working copy to whoever owns the repository storage, which the
   * entrypoint has already pointed at the user Apache and the daemons run as.
   *
   * git refuses to read a repository owned by another user, so a working copy
   * left owned by root fails every ref query with "detected dubious
   * ownership" - which is #369, reintroduced from the other end.
   */
  private function grantWorkingCopyOwnership($path) {
    $storage = dirname($path);

    $uid = fileowner($storage);
    $gid = filegroup($storage);
    if ($uid === false || $gid === false) {
      throw new Exception(pht(
        'Cannot read the ownership of repository storage "%s".',
        $storage));
    }

    if ($uid === posix_geteuid()) {
      return;
    }

    execx('chown -R %s %s', $uid.':'.$gid, $path);
  }

  /**
   * Run git in $cwd. $argv is a csprintf pattern and its arguments.
   */
  private function git($cwd, array $argv, array $env = array()) {
    $future = newv('ExecFuture', $argv)->setCWD($cwd);
    foreach ($env as $key => $value) {
      $future->updateEnv($key, $value);
    }
    return $future->resolvex();
  }

}
