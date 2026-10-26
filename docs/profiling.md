# Profiling

When a command is slower than it should be, `--profile` records where the time
went, and `phabfive profile` opens the recording in the
[Firefox Profiler](https://profiler.firefox.com/docs/):

```bash
phabfive --profile maniphest search --assigned=@me
phabfive profile
```

A command mostly waits: on Conduit, on a retry's backoff, on `PHAB_PACE`, on
you at a prompt. The profile shows each of those as a marker, and colours the
timeline by what the command was waiting on, so a slow search reads as one long
blue stretch per page, a struggling server as orange backoff between them.

Recording is off unless asked for, and costs nothing then.

## Recording

`--profile` records one command. To record every command, set `PHAB_PROFILE`:

```bash
PHAB_PROFILE=1 phabfive maniphest show T123
```

or put `PHAB_PROFILE: true` in `~/.config/phabfive.yaml`. A recorded command
says where it saved the profile, on stderr:

```
Profile saved to /home/me/.local/share/phabfive/profiles/profile-20261026-101500-4242.json, open it with: phabfive profile
```

`-q` leaves that line out. Shell completion, `--help`, `--version`,
`phabfive profile` itself and `phabfive repl` are never recorded.

## What a profile shows

| Track or marker | What it is |
|---|---|
| Loading Python modules | From the start of the command until it began running: importing phabfive and its libraries |
| Configuring and connecting | Reading the configuration and checking the connection, before the command's own work |
| Network | One marker per Conduit call, with the method, its parameters and how it ended |
| Sleep | A retry's backoff, `PHAB_PACE` between the writes of a batch, waiting for a repository |
| Waiting for input | A prompt, or `$EDITOR`, waiting for you |
| Cache | A lookup in the [cache](caching.md), and whether it hit |
| Log | Every log record, debug included, whatever `-v` or `-q` showed on the console |
| The command, such as `maniphest search` | The whole command, with its exit status |

The call tree adds the same time up by what it was spent on: `Conduit` and the
method below it, `Sleep` and its reason, `Waiting for input`, and phabfive's own
work at the top. The Network tab lists the Conduit calls with their durations.

## What a profile never holds

A profile is meant to be shareable, so some things are left out when it is
recorded rather than when it is shared:

- **Tokens.** `api.token` is never recorded, and a Conduit or Arcanist token in a
  log message is shortened to `api-…`
- **Text somebody wrote.** Descriptions, comments, content and other free text
  are replaced with `…`. A transaction is recorded by its type only, so a
  `maniphest.edit` shows `transactions[0][type]=description`, not the
  description
- **Secrets.** Parameters named for secrets or passwords are replaced, and a
  passphrase's secret is never part of a request
- **Answers.** A prompt is recorded by its question, never what was typed

What it does hold is the rest of each call's parameters - project names, PHIDs,
a search's query - and the names of the tasks and projects that appear in log
messages. Treat a profile like the output of the command that made it.

Profiles are written readable by you only.

## A summary in the terminal

`--summary` answers the usual question - where did the time go - without a
browser:

```bash
phabfive profile --summary             # the newest profile
phabfive profile --summary <file>
```

```
phabfive user whoami
Started 2026-10-26T23:48:23, took 0.42s, exit status 1

Where the time went
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━┓
┃ Activity               ┃   Time ┃ Share ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━┩
│ Loading Python modules │ 0.207s │   49% │
│ Sleep                  │ 0.163s │   38% │
│ phabfive's own work    │ 0.052s │   12% │
│ Conduit                │ 0.002s │    0% │
└────────────────────────┴────────┴───────┘

Conduit calls
┏━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┓
┃ Method        ┃ Calls ┃ Failed ┃  Total ┃ Slowest ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━━━┩
│ conduit.query │     1 │      1 │ 0.165s │  0.165s │
└───────────────┴───────┴────────┴────────┴─────────┘

Sleeps
┏━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ Reason                 ┃ Count ┃  Total ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│ retry 2: conduit.query │     1 │ 0.097s │
│ retry 1: conduit.query │     1 │ 0.066s │
└────────────────────────┴───────┴────────┘

Warnings and errors
  WARNING: conduit.query on 127.0.0.1:9: could not connect, retry 1 of 2 in 0.1s
  WARNING: conduit.query on 127.0.0.1:9: could not connect, retry 2 of 2 in 0.1s
```

**Where the time went** adds the run up by the call tree's top-level entries:
start-up, `Conduit`, `Sleep`, `Waiting for input`, `Cache`, and the rest as
phabfive's own work. A call's retries count as `Sleep`, not `Conduit`, so the two
always add up to the run. **Conduit calls** groups the calls by method, with
their retries included in Total. The summary is computed from the saved file,
so it works on a profile copied from someone else's machine.

`--format=json`, `jsonl` and `yaml` give the same summary as one record, for a
script or an agent:

```bash
phabfive --format=json profile --summary | jq '.Conduit[0]'
```

## Opening a profile

```bash
phabfive profile                 # the newest one
phabfive profile ~/.local/share/phabfive/profiles/profile-20261026-101500-4242.json
phabfive profile --list          # what is saved
phabfive --format=json profile --list
```

`phabfive profile` serves the file once from `127.0.0.1`, on a port the system
picks, and opens the profiler in your browser at an address that loads it from
there. The profile goes from your disk to your browser and nowhere else: the
profiler page is the same everywhere, only the data is local. It leaves your
machine only if you press the profiler's upload button.

The command waits until the profiler has fetched the file, five minutes at most
(`--timeout`), and then exits. `--no-browser` prints the address instead of
opening it. Over SSH, forward the port it names, or copy the file and open it
with `phabfive profile <file>` locally.

### Which profiler

Profiles open at `https://profiler.dynamist.cloud`, a copy of the Firefox
Profiler. Any other instance can open them too:

```bash
PHAB_PROFILER_ORIGIN=https://profiler.firefox.com phabfive profile
PHAB_PROFILER_ORIGIN=http://localhost:4242 phabfive profile    # one you run yourself
```

Chromium-based browsers may ask whether the profiler's page may access devices on
your local network, because it fetches the file from `127.0.0.1`. Allow it; the
only thing on offer there is the profile, and only until it has been fetched.

### Running the profiler yourself

The Firefox Profiler is a web page with no server behind it, so it runs just as
well from your own machine - offline, and without the local-network prompt,
since both the page and the profile are then on localhost. It needs Node.js
and Yarn:

```bash
git clone --branch production https://github.com/firefox-devtools/profiler.git
cd profiler
yarn install
yarn start                     # serves the profiler at http://localhost:4242
```

and in another terminal:

```bash
PHAB_PROFILER_ORIGIN=http://localhost:4242 phabfive profile
```

Put `PHAB_PROFILER_ORIGIN: http://localhost:4242` in `~/.config/phabfive.yaml` to
make it the default. The `production` branch is what profiler.firefox.com runs;
`git pull` and `yarn install` again to update it.

`yarn start` builds the profiler as it starts. To build it once and serve the
result, `yarn start-prod` writes the static site to `dist/` and serves it at
`http://localhost:4243`:

```bash
yarn start-prod
PHAB_PROFILER_ORIGIN=http://localhost:4243 phabfive profile
```

`dist/` can go behind any web server that answers unknown paths with
`index.html`: the `/from-url/...` address is a route inside the page, not a
file.

The profiler is licensed under the MPL-2.0. Running it for yourself places no
obligations on you; publishing a modified copy does.

## Configuration

| Setting | Default | Meaning |
|---|---|---|
| `PHAB_PROFILE` | `false` | Record every command, as `--profile` records one |
| `PHAB_PROFILE_KEEP` | `20` | How many profiles to keep; the oldest beyond it are deleted. `0` keeps them all |
| `PHAB_PROFILE_DIR` | `~/.local/share/phabfive/profiles` | Where profiles are saved |
| `PHAB_PROFILER_ORIGIN` | `https://profiler.dynamist.cloud` | The Firefox Profiler `phabfive profile` opens |

Like every setting they can go in the environment or in
`~/.config/phabfive.yaml`. The default directory follows the platform: on macOS
it is `~/Library/Application Support/phabfive/profiles`. `phabfive cache clear`
does not touch it.
