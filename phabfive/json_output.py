# -*- coding: utf-8 -*-
"""How JSON leaves phabfive.

Two formats share one set of record builders: ``json`` wraps the records in an
indented array, ``jsonl`` writes one compact object per line with no wrapper at
all (https://jsonlines.org/). Every place that serialises records goes through
here so the two can never drift apart.
"""

import json
import sys


def _exit_on_closed_pipe():
    """Leave quietly when the consumer stopped reading.

    ``head`` closing the pipe is normal, not an error. Closing stderr first
    stops the interpreter reporting the same broken pipe again while it
    flushes stdout on the way out.

    Callers that print by other means keep their own handlers; this one is
    here because jsonl flushes after every record, so it meets a closed pipe
    far more often than a single buffered write ever did.
    """
    sys.stderr.close()
    sys.exit(0)


def iter_records(records, output_format):
    """Serialise records, yielding each chunk that should be printed.

    ``jsonl`` yields one compact object per line, ``json`` yields the whole
    indented array as a single string. Callers that must keep the printing
    themselves - passphrase, which intentionally writes secrets to stdout and
    carries the static-analysis suppression for it - iterate this instead of
    calling :func:`emit_records`, so no suppression has to live in this
    module and blind it for every other caller.

    Parameters
    ----------
    records : list
        JSON-serialisable dictionaries, already stripped of internal keys.
    output_format : str
        Either 'jsonl' or anything else, which is treated as 'json'.

    Yields
    ------
    str
        A line for 'jsonl', or the one array for 'json'.
    """
    if output_format == "jsonl":
        for record in records:
            yield json.dumps(record, default=str)
    else:
        yield json.dumps(records, indent=2, default=str)


def emit_records(records, output_format):
    """Print already-built records.

    ``jsonl`` writes and flushes one line per record, so a reader downstream
    sees each record as it is written instead of waiting for the closing
    bracket. ``json`` writes the whole array in one go.

    Parameters
    ----------
    records : list
        JSON-serialisable dictionaries, already stripped of internal keys.
    output_format : str
        Either 'jsonl' or anything else, which is treated as 'json'.
    """
    try:
        for chunk in iter_records(records, output_format):
            print(chunk)
            sys.stdout.flush()
    except BrokenPipeError:
        _exit_on_closed_pipe()


def emit_record(record, output_format):
    """Print one already-built record that is a top-level object, not a list.

    Parameters
    ----------
    record : dict
        A JSON-serialisable dictionary.
    output_format : str
        Either 'jsonl' or anything else, which is treated as 'json'.
    """
    try:
        if output_format == "jsonl":
            print(json.dumps(record, default=str))
        else:
            print(json.dumps(record, indent=2, default=str))
        sys.stdout.flush()
    except BrokenPipeError:
        _exit_on_closed_pipe()
