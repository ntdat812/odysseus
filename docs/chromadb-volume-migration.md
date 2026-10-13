# ChromaDB Volume Migration — Upgrading from pre-#6166 installs

> [!IMPORTANT]
> This guide is only for Docker-based installs that were running before PR #6166
> was merged. Native installs are unaffected (they use `data/chroma/` on the
> host filesystem, which was always persisted correctly).

---

## What changed and why

Before PR #6166 the `chromadb-data` named volume was mounted at
`/chroma/chroma` inside the ChromaDB container. The current
`chromadb/chroma` image (1.0+) writes its database to `/data` instead —
configured by its built-in `/config.yaml` (`persist_path: "/data"`). Because
the image declares no `VOLUME` directive, **any data written to `/data` lived
only in the container's writable layer** and was invisible to the named volume.

PR #6166 corrects the mount to `/data`. After you pull this update and
recreate the ChromaDB container, the named volume will receive new writes
correctly. However, **embeddings that accumulated in the old writable layer
still live there** until the old container is removed — and they are lost the
moment it is.

This guide walks you through inspecting, backing up, and migrating that data
*before* you recreate the container.

---

## Step 0 — Identify your project name and container

Docker Compose prefixes volume and container names with the project name
(usually the directory name). Find yours:

```bash
# List running ChromaDB containers
docker ps --filter "ancestor=chromadb/chroma" \
  --format "table {{.ID}}\t{{.Status}}\t{{.Names}}"

# List the named volume
docker volume ls --filter "name=chromadb"
```

You will see something like `odysseus_chromadb-data` (volume) and
`odysseus-chromadb-1` (container). Substitute these for `<project>` in the
commands below.

---

## Step 1 — Check whether migration is needed

### 1a. Is the old container still running?

```bash
docker ps --filter "name=<project>-chromadb" \
  --format "table {{.ID}}\t{{.Status}}\t{{.Names}}"
```

- **Container is running** → proceed to Step 1b. The writable-layer data is
  still recoverable.
- **Container is stopped but not removed** → proceed to Step 1b. Stopped
  containers retain their writable layer.
- **Container has been removed** (e.g. after a prior `docker compose up`
  without this guide) → skip to [Re-indexing](#re-indexing-after-data-loss).

### 1b. Is there data in the writable layer?

```bash
docker exec <project>-chromadb-1 sh -c \
  "du -sh /data/ && find /data -type f"
```

Example of a container **with data to migrate**:
```
6.8M  /data/
/data/chroma.sqlite3
/data/e08e8058-5d17-4756-971f-eb233a796ab4/data_level0.bin
...
```

Example of a container **with no data** (fresh install or already recreated):
```
0     /data/
```

If `/data/` is empty or missing `chroma.sqlite3`, there is nothing to migrate —
skip to [Applying the update](#applying-the-update).

### 1c. Is there anything in the named volume?

The named volume was mounted at `/chroma/chroma` (the old, wrong path), so it
should be empty. Confirm:

```bash
docker run --rm \
  -v <project>_chromadb-data:/vol:ro \
  alpine find /vol -type f
```

A pre-#6166 install will show no files. If you see files here, you are running
a pre-1.0 ChromaDB image that wrote to `/chroma/chroma` — see
[Pre-1.0 image note](#pre-10-image-note) below.

---

## Step 2 — Back up the writable-layer data

**Do this before pulling the update or recreating any containers.**

Copy the entire `/data` directory out of the running container onto your host:

```bash
# Creates ./chromadb-backup-<date>/ on your host
BACKUP_DIR="./chromadb-backup-$(date +%Y%m%d-%H%M%S)"
docker cp <project>-chromadb-1:/data/. "$BACKUP_DIR/"
echo "Backed up to $BACKUP_DIR"
ls -lh "$BACKUP_DIR/"
```

Verify the backup contains `chroma.sqlite3`:

```bash
ls "$BACKUP_DIR/chroma.sqlite3"   # must exist
```

> [!CAUTION]
> Do not skip this step. Once the container is recreated (Step 4), the
> writable-layer data is gone permanently.

---

## Step 3 — Seed the named volume from the backup

The named volume (`<project>_chromadb-data`) is currently empty. Copy the
backup into it using a disposable Alpine container:

```bash
docker run --rm \
  -v <project>_chromadb-data:/target \
  -v "$BACKUP_DIR":/source:ro \
  alpine sh -c "cp -a /source/. /target/ && echo 'Migration complete' && ls -la /target/"
```

Expected output:
```
Migration complete
drwxr-xr-x  chroma.sqlite3
drwxr-xr-x  e08e8058-5d17-...
...
```

Verify the volume now contains `chroma.sqlite3`:

```bash
docker run --rm \
  -v <project>_chromadb-data:/vol:ro \
  alpine find /vol -type f
```

---

## Step 4 — Apply the update and recreate the ChromaDB container

Pull the updated Compose files and recreate **only the ChromaDB container**.
Do not use `--volumes` — that would wipe the named volume you just populated.

```bash
git pull
# For the standard configuration:
docker compose up -d --no-deps --force-recreate chromadb

# For the NVIDIA configuration:
# docker compose -f docker-compose.gpu-nvidia.yml up -d --no-deps --force-recreate chromadb

# For the AMD configuration:
# docker compose -f docker-compose.gpu-amd.yml up -d --no-deps --force-recreate chromadb
```

The new container mounts `chromadb-data` at `/data`, so it will immediately
see the data you migrated in Step 3.

---

## Step 5 — Verify the migration

### 5a. Confirm the new mount

```bash
docker inspect <project>-chromadb-1 \
  --format '{{range .Mounts}}{{.Name}} → {{.Destination}}{{"\n"}}{{end}}'
```

Expected output:
```
<project>_chromadb-data → /data
```

If you still see `→ /chroma/chroma`, the Compose files have not been updated —
check that `git pull` completed successfully.

### 5b. Confirm the data is present

```bash
docker exec <project>-chromadb-1 find /data -type f
```

You should see the same files as your backup.

### 5c. Query the collection count through ChromaDB's API

```bash
curl -s http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections | python3 -m json.tool
```

If the API returns your collections (e.g. `odysseus_rag_fastembed`,
`odysseus_memories_fastembed`, `odysseus_tool_index_fastembed`), the migration
succeeded. Open Odysseus and verify that Memory and RAG continue to function.

---

## Step 6 — Keep or discard the host backup

Once you have confirmed the migration in Step 5, the `./chromadb-backup-*/`
directory on your host is safe to remove. Keep it for a few days as a
rollback option.

---

## Rolling back

If something went wrong after Step 4:

1. Stop the new container: `docker compose stop chromadb` (no `--volumes`).
2. Clear the named volume and restore the backup:
   ```bash
   docker run --rm \
     -v <project>_chromadb-data:/target \
     alpine sh -c "rm -rf /target/*"
   docker run --rm \
     -v <project>_chromadb-data:/target \
     -v "$BACKUP_DIR":/source:ro \
     alpine sh -c "cp -a /source/. /target/"
   ```
3. Restart with the previous Compose file (via `git checkout` to the old
   commit, then `docker compose up -d chromadb`).

---

## Re-indexing after data loss

If the original container was already removed before you followed this guide,
the writable-layer data is gone. Your options:

- **RAG documents** — re-upload the source files through **Settings → Memory →
  RAG** (or drag them into the chat). Odysseus re-indexes each file on upload.
- **Semantic memory** — memories are also stored in the application database
  (`data/app.db`). The vector embeddings are regenerated automatically when
  Odysseus restarts and ChromaDB is healthy.
- **Tool index** — rebuilt automatically on first use.

No application data (conversations, settings, documents) lives in ChromaDB;
only the vector representations do.

---

## Pre-1.0 image note

If you were running a ChromaDB image older than 1.0 (which wrote its store to
`/chroma/chroma` rather than `/data`), your named volume may contain data at
the old path. Check with:

```bash
docker run --rm \
  -v <project>_chromadb-data:/vol:ro \
  alpine find /vol -type f
```

If files are present, they are already in the volume; the migration path above
still applies, but use `/vol` as the source rather than copying from the
container's writable layer.

---

## Fresh installs

If you are setting up Odysseus for the first time with this version of the
Compose files, no migration is needed. The `chromadb-data` volume will be
created empty and ChromaDB will populate it at `/data` from the start.
