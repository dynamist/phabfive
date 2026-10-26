# The local Phorge's admin SSH key

`phabfive-insecure` is a **publicly known** private key, committed on purpose like the
API token `api-supersecr3tapikeyfordevelop1`. The `sshkeys` seed module registers its
public half for the admin of the disposable local Phorge, so git over SSH works from
every worktree against the one shared instance.

Never authorize it anywhere else, and never load it into an agent.

ssh refuses a private key that others can read, and git checks this one out 0644, so
use the 0600 copy `make deploy` and `make ssh-forward` write to `.k8s/phabfive-insecure`.
See docs/phorge-setup.md, "Git over SSH".
