# Mutation testing, batch 329

`orchestration/windows_zones.py` (Windows time zone ids and the IANA zone each one means, generated from Unicode CLDR) was given to this batch and not run: it is one dict of string constants and has no code, so `sidecar/tools/mutate.py` (at `main` 3424d8f33, `--cap 100000 --seed 7 --kinds all`) makes 0 mutants of it. Its table is read by test_pipeline_time_zones through the pipeline functions that use it. No score, no new tests and no doc beyond this note.
