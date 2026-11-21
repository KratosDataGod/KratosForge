# Delta writes read back by Spark (2026-11-21)

A differential check of the tables KratosForge writes under the modern profile and each per-table choice
(delta_features), with Spark 3.5.5 and Delta 3.3.2 standing in for Fabric Spark. It is made by
`sidecar/tools/spark_differential_check.py`, by hand, since CI has no Spark.

## What was run

Each Spark-made fixture in `sidecar/tests/fixtures/delta_spark` was copied to its own folder for every write,
and the write ran through the product path:

- LocalStore `write` append (ids 100, 101), `write` overwrite (id 7) and `upsert` on id (id 0 changed, id 100
  added), with `delta_policies` set from each choice (`P.table_policy`);
- local SQL `INSERT`, `UPDATE ... WHERE id = 0` and `DELETE ... WHERE id = 4` through LocalData's engine,
  which has no per-table choice and writes with the default;
- `publish.publish` append (id 100) and overwrite (id 7) to a local path, with `within` set from each choice.

After each write that went through, the rows delta-rs reads were compared with the rows Spark reads (column
names, values, nulls, and types mapped from Arrow to Spark's), and with the rows the write should leave.
Spark's DESCRIBE DETAIL protocol was recorded and checked against delta-rs's. Spark then ran an `UPDATE` (the
highest id's second column to -5) and a `DELETE` (the lowest id) on a copy; its result had to be the expected
one, and delta-rs had to read Spark's result back the same. For each write the engine refused, a SHA-256
snapshot of every file in the table was taken before and after.

## Result

- 138 combinations: 60 written, 78 refused.
- Of the 60 written, 60 matched Spark in every check, and 0 did not.
- Every refusal left the table byte for byte unchanged: yes.
- every_reader lets no write through on these fixtures: each one has deletion vectors or column mapping,
  which the conservative profile refuses. Column mapping by ID (cm_id) is refused under every choice.

No mismatch was found.

## Protocols

The protocol Spark's DESCRIBE DETAIL shows after the engine's writes, per fixture (reader/writer version and
table features). No engine write changed a fixture's protocol. For cm_legacy, a legacy writer-5 table with no feature lists, Spark lists
every feature writer version 5 implies (changeDataFeed, checkConstraints, generatedColumns among them); none of
them is in use, which is why the modern profile lets it be written (delta_profile.problems).

Spark logs `ERROR DeltaParquetFileFormat: Failed to translate filter EqualTo(__delta_internal_is_row_deleted,0)`
while it reads a table with deletion vectors. It isn't a failure: Delta can't push that filter down into
Parquet and applies it after the scan instead, and the rows it returns are the ones compared here.

- dv: 3/7 (appendOnly, deletionVectors, invariants)
- dv_part: 3/7 (appendOnly, deletionVectors, invariants)
- cm_name: 2/7 (appendOnly, columnMapping, invariants)
- dv_cm: 3/7 (appendOnly, columnMapping, deletionVectors, invariants)
- cm_legacy: 2/5 (appendOnly, changeDataFeed, checkConstraints, columnMapping, generatedColumns, invariants)

## The matrix

"Refused" in the Match column means the engine refused the write and left the table unchanged, as the
choice says it should.

| Fixture | Operation | Choice | Engine rows | Spark rows | Match | Notes |
|---|---|---|---|---|---|---|
| dv | store append | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 3 rows deleted by deletion vectors |
| dv | store append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv | store append | deletion_vectors | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 3 rows deleted by deletion vectors |
| dv | store append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv | store overwrite | default | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | store overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv | store overwrite | deletion_vectors | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | store overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv | store upsert | default | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | store upsert | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv | store upsert | deletion_vectors | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | store upsert | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv | sql insert | default | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 3 rows deleted by deletion vectors |
| dv | sql update | default | 7 | 7 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | sql delete | default | 6 | 6 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | publish append | default | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 3 rows deleted by deletion vectors |
| dv | publish append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv | publish append | deletion_vectors | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 3 rows deleted by deletion vectors |
| dv | publish append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv | publish overwrite | default | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | publish overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv | publish overwrite | deletion_vectors | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv | publish overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_part | store append | default | 10 | 10 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_part | store append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv_part | store append | deletion_vectors | 10 | 10 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_part | store append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_part | store overwrite | default | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_part | store overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv_part | store overwrite | deletion_vectors | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_part | store overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_part | store upsert | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 1 rows deleted by deletion vectors |
| dv_part | store upsert | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv_part | store upsert | deletion_vectors | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 1 rows deleted by deletion vectors |
| dv_part | store upsert | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_part | sql insert | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_part | sql update | default | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 1 rows deleted by deletion vectors |
| dv_part | sql delete | default | 7 | 7 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 1 rows deleted by deletion vectors |
| dv_part | publish append | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_part | publish append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv_part | publish append | deletion_vectors | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_part | publish append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_part | publish overwrite | default | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_part | publish overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['deletionVectors']; writer features ['appendOnly', 'deletionVectors', 'invariants']; delta.en |
| dv_part | publish overwrite | deletion_vectors | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_part | publish overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| cm_name | store append | default | 12 | 12 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | store append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=name. It uses column m |
| cm_name | store append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and e |
| cm_name | store append | column_mapping | 12 | 12 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | store overwrite | default | 1 | 1 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | store overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=name. It uses column m |
| cm_name | store overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and e |
| cm_name | store overwrite | column_mapping | 1 | 1 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | store upsert | default | 11 | 11 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | store upsert | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=name. It uses column m |
| cm_name | store upsert | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and e |
| cm_name | store upsert | column_mapping | 11 | 11 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | sql insert | default | 11 | 11 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | sql update | default | 10 | 10 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | sql delete | default | 9 | 9 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | publish append | default | 11 | 11 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | publish append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=name. It uses column m |
| cm_name | publish append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and e |
| cm_name | publish append | column_mapping | 11 | 11 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | publish overwrite | default | 1 | 1 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_name | publish overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=name. It uses column m |
| cm_name | publish overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and e |
| cm_name | publish overwrite | column_mapping | 1 | 1 | yes | protocol 2/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_id | store append | default | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL databases, Python not |
| cm_id | store append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=id. It uses column map |
| cm_id | store append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse e |
| cm_id | store append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL database |
| cm_id | store overwrite | default | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL databases, Python not |
| cm_id | store overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=id. It uses column map |
| cm_id | store overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse e |
| cm_id | store overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL database |
| cm_id | store upsert | default | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL databases, Python not |
| cm_id | store upsert | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=id. It uses column map |
| cm_id | store upsert | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse e |
| cm_id | store upsert | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL database |
| cm_id | sql insert | default | - | - | refused | table unchanged (SHA-256 of every file); ValueError: dbo.cm_id isn't written here: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, K |
| cm_id | sql update | default | - | - | refused | table unchanged (SHA-256 of every file); ValueError: dbo.cm_id isn't changed here: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, K |
| cm_id | sql delete | default | - | - | refused | table unchanged (SHA-256 of every file); ValueError: dbo.cm_id isn't changed here: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, K |
| cm_id | publish append | default | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL databases, Python not |
| cm_id | publish append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=id. It uses column map |
| cm_id | publish append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse e |
| cm_id | publish append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL database |
| cm_id | publish overwrite | default | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the modern Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL databases, Python not |
| cm_id | publish overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 7 > 2; writer features ['appendOnly', 'columnMapping', 'invariants']; delta.columnMapping.mode=id. It uses column map |
| cm_id | publish overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: writer features ['columnMapping']; delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse e |
| cm_id | publish overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: delta.columnMapping.mode=id. It uses column mapping by ID, which Fabric's SQL analytics endpoint, lakehouse explorer, Dataflows Gen2, KQL database |
| dv_cm | store append | default | 10 | 10 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_cm | store append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['columnMapping', 'deletionVectors']; writer features ['appendOnly', 'columnMapping', 'deletio |
| dv_cm | store append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: reader features ['columnMapping']; writer features ['columnMapping']; delta.columnMapping.mode=name. It uses deletion vectors, which Fabric Pyth |
| dv_cm | store append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_cm | store overwrite | default | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_cm | store overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['columnMapping', 'deletionVectors']; writer features ['appendOnly', 'columnMapping', 'deletio |
| dv_cm | store overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: reader features ['columnMapping']; writer features ['columnMapping']; delta.columnMapping.mode=name. It uses deletion vectors, which Fabric Pyth |
| dv_cm | store overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_cm | store upsert | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_cm | store upsert | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['columnMapping', 'deletionVectors']; writer features ['appendOnly', 'columnMapping', 'deletio |
| dv_cm | store upsert | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: reader features ['columnMapping']; writer features ['columnMapping']; delta.columnMapping.mode=name. It uses deletion vectors, which Fabric Pyth |
| dv_cm | store upsert | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_cm | sql insert | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_cm | sql update | default | 8 | 8 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_cm | sql delete | default | 7 | 7 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_cm | publish append | default | 9 | 9 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same; 2 rows deleted by deletion vectors |
| dv_cm | publish append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['columnMapping', 'deletionVectors']; writer features ['appendOnly', 'columnMapping', 'deletio |
| dv_cm | publish append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: reader features ['columnMapping']; writer features ['columnMapping']; delta.columnMapping.mode=name. It uses deletion vectors, which Fabric Pyth |
| dv_cm | publish append | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| dv_cm | publish overwrite | default | 1 | 1 | yes | protocol 3/7; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| dv_cm | publish overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 3 > 1; writer version 7 > 2; reader features ['columnMapping', 'deletionVectors']; writer features ['appendOnly', 'columnMapping', 'deletio |
| dv_cm | publish overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: reader features ['columnMapping']; writer features ['columnMapping']; delta.columnMapping.mode=name. It uses deletion vectors, which Fabric Pyth |
| dv_cm | publish overwrite | column_mapping | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the column mapping only Delta profile: reader features ['deletionVectors']; writer features ['deletionVectors']; delta.enableDeletionVectors=true. It uses deletion vectors, which Fabric |
| cm_legacy | store append | default | 13 | 13 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | store append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 5 > 2; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and ev |
| cm_legacy | store append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read |
| cm_legacy | store append | column_mapping | 13 | 13 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | store overwrite | default | 1 | 1 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | store overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 5 > 2; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and ev |
| cm_legacy | store overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read |
| cm_legacy | store overwrite | column_mapping | 1 | 1 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | store upsert | default | 12 | 12 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | store upsert | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 5 > 2; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and ev |
| cm_legacy | store upsert | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read |
| cm_legacy | store upsert | column_mapping | 12 | 12 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | sql insert | default | 12 | 12 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | sql update | default | 11 | 11 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | sql delete | default | 10 | 10 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | publish append | default | 12 | 12 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | publish append | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 5 > 2; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and ev |
| cm_legacy | publish append | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read |
| cm_legacy | publish append | column_mapping | 12 | 12 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | publish overwrite | default | 1 | 1 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
| cm_legacy | publish overwrite | every_reader | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the Fabric-safe Delta profile: reader version 2 > 1; writer version 5 > 2; delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and ev |
| cm_legacy | publish overwrite | deletion_vectors | - | - | refused | table unchanged (SHA-256 of every file); ProfileViolation: Table is outside the deletion vectors only Delta profile: delta.columnMapping.mode=name. It uses column mapping by name, which Fabric Python notebooks, pipelines and eventstreams can't read |
| cm_legacy | publish overwrite | column_mapping | 1 | 1 | yes | protocol 2/5; Spark's UPDATE and DELETE ran and delta-rs reads their result the same |
