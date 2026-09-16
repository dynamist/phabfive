# -*- coding: utf-8 -*-
"""On-disk cache for the API lookups that shell completion repeats.

Every API-backed completion queries the server on each TAB, and building a
Phabfive() to do it costs two round trips of its own (update_interfaces and
verify_connection) before the lookup even starts. A TAB pressed twice, which
is how bash is normally driven, pays all of that twice.

Call sites opt in. Nothing here hooks the Phabricator client, so an API
response can only be cached by code that was written to cache it; that is what
keeps passphrase values out of the cache by construction rather than by a
denylist that someone has to remember to update.

Every operation is best effort. A missing, unreadable, corrupt or stale entry
behaves as a miss, never as an error, because completion must keep working when
the cache does not.
"""

# python std lib
import hashlib
import json
import os
import re
import time
from urllib.parse import urlparse

# phabfive imports
from phabfive.constants import (
    CACHE_SCHEMA_VERSION,
    CACHE_TTL_DEFAULT,
    CACHE_TTLS,
)

# 3rd party imports
import appdirs

# Returned by get() when there is nothing usable to return. A sentinel rather
# than None so that a cached None or [] is still a hit.
MISS = object()

# Values that mean "off" for PHAB_CACHE, whether they arrive as a string from
# the environment or as a real bool from a yaml config
_FALSEY = {"0", "false", "no", "off", "", "none"}

# Keep a namespace from growing without bound; one typed prefix leaves one file
MAX_ENTRIES_PER_NAMESPACE = 200


def _truthy(value, default=True):
    """Interpret a config value that may be a bool, an int or a string."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in _FALSEY


def _int(value, default=0):
    """Interpret a config value that may be an int or a string."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def enabled():
    """Whether caching is switched on, without loading any configuration.

    PHAB_CACHE is read straight from the environment so that PHAB_CACHE=0
    costs nothing and takes effect before anything touches the filesystem.
    The same key is also honoured from a yaml config in instance_dir(), which
    is loading configuration anyway.
    """
    return _truthy(os.environ.get("PHAB_CACHE"))


def _read_config():
    """Return the phabfive configuration, or None when it cannot be read."""
    try:
        # Imported here so that a cache miss is what pays for importing
        # phabfive.core, not every phabfive invocation
        from phabfive.core import Phabfive

        conf, _ = Phabfive.read_config()
        return conf
    except Exception:
        return None


def root(conf=None):
    """Return the directory holding every instance's cache."""
    configured = os.environ.get("PHAB_CACHE_DIR") or (conf or {}).get("PHAB_CACHE_DIR")
    base = configured or appdirs.user_cache_dir("phabfive")
    return os.path.join(base, f"v{CACHE_SCHEMA_VERSION}")


def _slug(url, token):
    """Name a directory for one instance, readably and unambiguously.

    The netloc makes the directory recognisable; the digest makes it unique.
    The token is part of the digest so that two accounts on the same host do
    not share entries and so that rotating a token invalidates what was cached
    under the old one. The token itself is never written out.
    """
    netloc = urlparse(url).netloc or "unknown"
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", netloc).lower()[:40]
    digest = hashlib.sha256(f"{url}\0{token}".encode()).hexdigest()[:8]
    return f"{safe}-{digest}"


def _instance_dir(conf):
    """Return where this configuration's entries live, or None without a URL."""
    url = (conf or {}).get("PHAB_URL")
    if not url:
        return None
    return os.path.join(root(conf), _slug(url, (conf or {}).get("PHAB_TOKEN") or ""))


def instance_dir():
    """Return the cache directory for the configured instance, or None.

    None means "do not cache": the cache is switched off, the configuration
    could not be read, or no PHAB_URL is configured to key entries by.
    """
    if not enabled():
        return None

    conf = _read_config()
    if not conf or not _truthy(conf.get("PHAB_CACHE")):
        return None

    return _instance_dir(conf)


def _ttl_from(conf, namespace):
    """Return the TTL for a namespace, honouring a PHAB_CACHE_TTL override."""
    override = _int((conf or {}).get("PHAB_CACHE_TTL"), 0)
    if override > 0:
        return override
    return CACHE_TTLS.get(namespace, CACHE_TTL_DEFAULT)


def ttl_for(namespace):
    """Return the TTL for a namespace, honouring a PHAB_CACHE_TTL override."""
    return _ttl_from(_read_config(), namespace)


def context(namespace):
    """Resolve where a namespace's entries live and how long they stay fresh.

    One configuration read for a caller about to make several lookups, which
    is what prefix narrowing does - reading it per lookup costs more than the
    round trips the cache is there to save. Returns (None, 0) when nothing
    should be cached.
    """
    if not enabled():
        return None, 0

    conf = _read_config()
    if not conf or not _truthy(conf.get("PHAB_CACHE")):
        return None, 0

    return _instance_dir(conf), _ttl_from(conf, namespace)


def _entry_path(directory, namespace, key):
    """Locate the file holding one entry.

    Keys are arbitrary text - slashes, "..", unicode, any length - so the
    filename is a digest. The key itself is stored inside the file and checked
    on read, which turns a digest collision into a miss rather than a wrong
    answer.
    """
    name = hashlib.sha256(key.encode()).hexdigest()[:32]
    return os.path.join(directory, namespace, f"{name}.json")


def get(namespace, key, ttl=None, directory=None):
    """Return the cached value for key, or MISS.

    MISS covers every way this can fail to produce the value that was stored:
    no entry, an expired one, a schema from another phabfive, a digest
    collision, a truncated write, or a directory that cannot be read.

    A caller making several lookups in a row passes the directory it already
    resolved, so that reading the configuration is not repeated per lookup.
    """
    try:
        if directory is None:
            directory = instance_dir()
        if directory is None:
            return MISS

        with open(_entry_path(directory, namespace, key), "rb") as stream:
            entry = json.loads(stream.read().decode("utf-8"))

        if entry.get("version") != CACHE_SCHEMA_VERSION:
            return MISS
        if entry.get("key") != key:
            return MISS

        # Against the caller's TTL, not the one that was stored, so that
        # lowering PHAB_CACHE_TTL takes effect at once
        if ttl is None:
            ttl = ttl_for(namespace)
        if time.time() - float(entry["created"]) > float(ttl):
            return MISS

        return entry["value"]
    except Exception:
        return MISS


def set(namespace, key, value, ttl=None, directory=None):
    """Store a value, best effort. Never raises, never reports failure."""
    try:
        if directory is None:
            directory = instance_dir()
        if directory is None:
            return

        if ttl is None:
            ttl = ttl_for(namespace)

        namespace_dir = os.path.join(directory, namespace)
        _make_private_dirs(namespace_dir)

        path = _entry_path(directory, namespace, key)
        payload = json.dumps(
            {
                "version": CACHE_SCHEMA_VERSION,
                "created": time.time(),
                "ttl": ttl,
                "key": key,
                "value": value,
            }
        ).encode("utf-8")

        _write_atomically(path, payload)
        _prune(namespace_dir)
    except Exception:
        return


def cached_call(namespace, key, produce, ttl=None):
    """Return the cached value for key, or produce and store it.

    produce() returning None means the lookup failed and nothing should be
    stored - caching a failure would silence completion for a whole TTL.
    """
    cached = get(namespace, key, ttl)
    if cached is not MISS:
        return cached

    value = produce()
    if value is not None:
        set(namespace, key, value, ttl)
    return value


def _make_private_dirs(namespace_dir):
    """Create the cache directories, readable only by their owner.

    makedirs' mode argument is masked by umask, so the bits are set afterwards.
    The four levels this module owns are the namespace and its three parents:
    the instance directory, the schema directory and the cache base.
    """
    os.makedirs(namespace_dir, exist_ok=True)
    if os.name == "nt":
        # Windows uses ACLs, not Unix permission bits
        return

    level = namespace_dir
    for _ in range(4):
        try:
            os.chmod(level, 0o700)
        except OSError:
            pass
        level = os.path.dirname(level)


def _write_atomically(path, payload):
    """Replace a file in one step, so a reader never sees a half-written entry.

    Concurrent completion processes - a double TAB, two terminals - write the
    same entry without coordinating, which os.replace makes safe.
    """
    tmp = f"{path}.{os.getpid()}.tmp"
    mode = 0o600 if os.name != "nt" else 0o666
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _prune(namespace_dir):
    """Keep a namespace bounded, dropping the least recently written first.

    Runs on the miss path, which is already the slow one.
    """
    try:
        entries = [
            os.path.join(namespace_dir, name)
            for name in os.listdir(namespace_dir)
            if name.endswith(".json")
        ]
        if len(entries) <= MAX_ENTRIES_PER_NAMESPACE:
            return

        entries.sort(key=lambda p: os.path.getmtime(p))
        for path in entries[: len(entries) - MAX_ENTRIES_PER_NAMESPACE]:
            try:
                os.unlink(path)
            except OSError:
                pass
    except OSError:
        return


def clear(all_instances=False):
    """Remove cached entries, returning how many files were removed.

    all_instances works without valid credentials, which is what somebody
    reaches for when something is wrong.
    """
    import shutil

    conf = _read_config()

    if all_instances:
        target = root(conf)
    else:
        # Not instance_dir(): caching may be switched off now, yet entries from
        # before it was switched off are exactly what needs clearing. None here
        # means there is no configured instance to clear.
        target = _instance_dir(conf)
        if target is None:
            return None

    if not os.path.isdir(target):
        return 0

    removed = sum(len(files) for _, _, files in os.walk(target))
    shutil.rmtree(target, ignore_errors=True)
    return removed


def describe():
    """Summarise what is cached, for `phabfive cache info`.

    Reports sizes and ages only; cached values are never included.
    """
    conf = _read_config() or {}
    url = conf.get("PHAB_URL")
    directory = _instance_dir(conf)

    if os.environ.get("PHAB_CACHE") is not None and not enabled():
        state, why = False, "disabled by PHAB_CACHE in the environment"
    elif not _truthy(conf.get("PHAB_CACHE")):
        state, why = False, "disabled by PHAB_CACHE in the configuration"
    elif not url:
        state, why = False, "no PHAB_URL is configured"
    else:
        state, why = True, "enabled"

    namespaces = []
    now = time.time()
    if directory and os.path.isdir(directory):
        for namespace in sorted(os.listdir(directory)):
            namespace_dir = os.path.join(directory, namespace)
            if not os.path.isdir(namespace_dir):
                continue
            ages, size, count = [], 0, 0
            for name in os.listdir(namespace_dir):
                path = os.path.join(namespace_dir, name)
                try:
                    size += os.path.getsize(path)
                    ages.append(now - os.path.getmtime(path))
                    count += 1
                except OSError:
                    continue
            if not count:
                continue
            namespaces.append(
                {
                    "Namespace": namespace,
                    "Entries": count,
                    "Size": size,
                    "TTL": ttl_for(namespace),
                    "Oldest": int(max(ages)),
                    "Newest": int(min(ages)),
                }
            )

    return {
        "Enabled": state,
        "Reason": why,
        "Path": directory or root(conf),
        "Namespaces": namespaces,
    }
