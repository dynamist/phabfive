# Specs that are wrong on purpose

Every file in this directory fails validation, each in exactly one way, and
each is named after the `code` the failure is reported under. They are the
fixtures `tests/test_spec_broken_corpus.py` asserts against, and they are
documentation by example: this is what each error looks like in a file, next
to the report it produces.

    phabfive spec validate specs/broken/undefined-variable.yaml

Nothing here is a template to copy. The directory is excluded from the walk
that requires every shipped spec to validate cleanly - by the walk's glob, so
the exclusion is one fact in one place rather than a skip in each test.

| File | Code | Layer |
|---|---|---|
| `undefined-variable.yaml` | `undefined-variable` | offline |
| `duplicate-local-id.yaml` | `duplicate-local-id` | offline |
| `dangling-local-id.yaml` | `unknown-local-id` | offline |
| `unknown-user.yaml` | `unknown-user` | online |

The first three need no instance: they are questions a file answers about
itself. The fourth needs one, because whether `nobody.here` is a user is not
something the file can know - which is the whole reason validation has two
layers.
