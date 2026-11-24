# Mutation testing, batch 161

A boundary re-score of eleven modules scored before `mutate.py` made boundary mutants (batch 36). At `main` 4ddd3662 (2026-11-18), `sidecar/tools/mutate.py --kinds boundary` finds no `<`, `<=`, `>` or `>=` comparison to mutate in any of them, so their earlier scores leave nothing out and no test was added: `sql_policy.py`, `sql_dialects.py`, `spark_struct_fields.py`, `sealed_settings.py`, `schema_cache.py`, `localdata/transform.py`, `integrations/policy.py`, `fabric_git_service.py`, `durable.py`, `delta_dml.py` and `appstudio/policy.py` each have 0 boundary mutants.

