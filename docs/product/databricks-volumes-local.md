# Unity Catalog volumes on this PC: design (Preview, engine side)

Approved by Chris. Screen: Workspaces > Databricks > Volumes on this PC (Preview, `app/ui/dbx-volumes.js`), from UX's
approved mockup (`ux/uc-volumes-local/mockup.html`), with its RPCs in `sidecar/fabric_desktop/databricks_volumes_service.py`. User docs:
[engines.md](../engines.md#unity-catalog-volumes-on-this-pc-preview). Code: `sidecar/fabric_desktop/databricks_volumes.py`.

## Sources (docs.databricks.com, read 2026-11-24)

- What are Unity Catalog volumes? and Work with files in volumes (`/aws/en/volumes/volume-files`): the path
  `/Volumes/<catalog>/<schema>/<volume>/<path>`, `dbfs:/Volumes/...` for the CLI, `dbutils.fs`, `%fs`, `os`,
  `spark.read...load`, `SELECT * FROM csv.`/Volumes/...``; "only the `LIST` command is supported" in notebooks and
  the SQL editor, `PUT INTO`/`GET`/`REMOVE` only through SQL connectors and drivers.
- CREATE VOLUME: `CREATE [EXTERNAL] VOLUME [IF NOT EXISTS] name [LOCATION path] [COMMENT text]`; error class
  `VOLUME_ALREADY_EXISTS`. DROP VOLUME: `DROP VOLUME [IF EXISTS] name`; a missing volume "raises an error" (no class
  named); managed files are kept 7 days for UNDROP. SHOW VOLUMES: `[{FROM|IN} schema] [[LIKE] pattern]`, columns
  `database`, `volumeName`; `*` matches any characters, `|` separates patterns, trimmed, case-insensitive.
  DESCRIBE VOLUME: `name`, `catalog`, `database`, `owner`, `storageLocation`, `volumeType`, `comment`. ALTER VOLUME:
  `RENAME TO` ("the schema must not change"), `[SET] OWNER TO`, `SET TAGS`, `UNSET TAGS`; COMMENT ON VOLUME is its own
  statement.
- LIST: `LIST url [WITH (CREDENTIAL c)] [LIMIT n]`, columns `path`, `name`, `size` (0 for directories),
  `modification_time` (milliseconds).
- read_files: `read_files(path [, key => value] ...)`; formats; a folder is read recursively; without a schema "the
  CSV files are assumed to have headers"; a rescued data column by default, dropped by `schemaEvolutionMode =>
  'none'`. Its default name `_rescued_data` is from the Auto Loader schema page.
- Names: Unity Catalog stores names in lowercase; a name can't hold `.`, space, `/`, control characters or DEL, and
  is at most 255 characters.
- Databricks Utilities: Python `dbutils.fs.ls('/Volumes/...')` gives `path='dbfs:/Volumes/...'`; `put` prints
  "Wrote N bytes."; `head` prints "[Truncated to first N bytes]".

The error-classes page names no volume-not-found class, so that message carries none (no class is made up).

## Decisions

1. **Where the files live.** `%LOCALAPPDATA%\FabricDesktop\volumes\main\<schema>\<volume>`: the app's data folder
   (`service.data_dir()`), as the app keeps its other stores, which `readiness` asks IT to keep on this PC and out
   of OneDrive (the OneDrive rule, tests/test_onedrive_folders.py). A root in OneDrive refuses CREATE VOLUME with
   `known_folders.onedrive_refusal`. UX's mockup shows `KratosForge\volumes`; the repo's helper says `FabricDesktop`,
   so that is used. Volumes outlive a SQL session and are shared by every notebook and SQL cell on this PC, as a
   workspace's volumes are. A volume's comment, owner (the signed-in Windows user) and creation time are in
   `.<volume>.volume.json` beside its folder.
2. **Managed only.** EXTERNAL and LOCATION are refused by name; so are `LIST`/`read_files` of any non-`/Volumes` URL.
   Nothing reaches cloud storage.
3. **Catalogs as WR-316.** `main`, `workspace`, `hive_metastore`, `spark_catalog` are the session's catalog, stored
   as `main`; anything else is refused by name, and the schema `main` is refused as for tables. A schema must exist
   in the session (or already hold volumes on this PC) for CREATE VOLUME and SHOW VOLUMES IN, else
   `SCHEMA_NOT_FOUND`; `default` always exists.
4. **SQL runs in its place.** Volume statements are lifted out before sqlglot (which can't read DESCRIBE VOLUME or
   LIST) and written as DuckDB SQL that calls the engine's functions on the session connection, so they run in
   order with the cell's other statements, and each change runs once. read_files is read before the cell runs, on
   a connection of its own that may open only the files named (`allowed_paths`, then external access off); the
   session's connection stays closed to files.
5. **DESCRIBE's storageLocation** is the volume's folder as `file:C:/...` (the page's managed example shows a
   location; its column text says NULL for managed). DROP VOLUME deletes at once: nothing keeps a copy, so UNDROP is
   refused.
6. **Python `open()`** is left alone: the kernel has no hook for builtins, and shadowing `open` alone would miss
   `os`, pandas and the rest.

## Security

A path is read by `parse` before the disk is touched: `/` is the only separator; `.`/`..`, backslashes, colons
(drives, streams), NUL and control characters, `* ? " < > |`, a final dot or space and device names (CON, NUL,
COM1...) are refused by name, so neither a drive nor a UNC path (`\\server\share`, `//server`) can come out of one,
consistent with dbutils_local's UNC handling. `local_path` refuses a volume folder (or its schema or catalog folder)
that is a link, and any path whose `realpath` (symlinks and junctions followed) is outside the volume's folder.
`check_tree` refuses a folder holding any link before a recursive copy, move, remove, read_files or Spark read; DROP
removes links without following them. The volume's own folder can't be removed or moved through dbutils.fs.
A link swapped in between the check and the use (a race) isn't guarded against; only the person's own processes
can write the folder.

## The screen

`databricks_volumes_service.py` gives the screen its RPCs: `databricks_volumes(schema=None)`,
`databricks_volume_describe(schema, volume)` and `databricks_volume_files(path, limit=None)` only read (they skip
the audit); `databricks_volume_create(schema, volume, comment=None)`, `databricks_volume_rename(schema, volume,
new_name)`, `databricks_volume_set_comment(schema, volume, comment=None)`, `databricks_volume_new_folder(path)`,
`databricks_volume_upload(source, path, overwrite=False)`, `databricks_volume_download(path, save_to,
overwrite=False)`, `databricks_volume_delete(paths, confirm=False)`, `databricks_volume_drop(schema, volume,
confirm=False)` and `databricks_volume_reveal(path)` are audited. Every volume path goes through `local_path`; a
folder is deleted only after `check_tree`; drop and delete need `confirm` (the screen's red confirm dialog sends
it); upload and download never replace a file without `overwrite`. A download's target is refused in OneDrive
(`known_folders.onedrive_refusal`) and inside the volumes' own folder. The shell's `pick_file` shows the Windows
file dialog for Upload and Download.
