# ADR-005 · Docker storage layout for the competition host

## Context

LEExtractor must survive `docker compose down/up`, image upgrades and a host
reboot on a 40 GB Ubuntu 24.04 server without losing user projects, re-downloading
models or silently dropping the embedding cache. Before v0.9.13 the app wrote
wherever its source tree happened to be, which inside a container is the image
layer — data would vanish on recreate.

## Decision

Four host directories, mounted into the container and owned by the non-root
runtime user (`leextractor`, uid 10001):

```
/opt/leextractor/{data,models,cache,logs}   ->   /data /models /cache /logs
```

The application learns them only from the environment
(`LEEXTRACTOR_DATA_DIR`, `_MODEL_DIR`, `_CACHE_DIR`, `_LOG_DIR`). No path is
hard-coded, and nothing is written to `/tmp`, `/root` or the image layer.

## Alternatives

1. **Named Docker volumes** — survive recreate, but are awkward to back up and to
   inspect; the competition needs a copy-paste backup of `data/` and `.env`.
2. **Single volume with subdirectories** — fewer mounts, but then one read-only
   or full mount breaks models *and* cache *and* data together.
3. **Bind mounts under `/opt/leextractor`** — chosen.

## Reason

Bind mounts make the backup story trivial (`tar czf backup.tgz data .env`),
keep the four failure domains separate (see ADR-006 for the degradation rules),
and let a human `ls` the models directory to prove Mode A (pre-installed models)
worked.

## Consequences

* The deployment must create and `chown` the four directories; DEPLOYMENT.md says so.
* A read-only `cache/` degrades to a temporary cache instead of failing (config.py).
* A read-only `data/` is fatal by design: `/api/health/ready` returns 503.

## Rollback

Remove the `volumes:` block and the `LEEXTRACTOR_*_DIR` variables; the app falls
back to its in-tree defaults (previous behaviour). No data migration is needed
because the previous behaviour never persisted projects inside containers.
