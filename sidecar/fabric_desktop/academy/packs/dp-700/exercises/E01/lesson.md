# Right-size Spark for three teams: starter pool, custom pool, autoscale and high concurrency

One workspace serves analysts, a nightly ETL job and data scientists. Someone made a big fixed pool the default for everyone: analysts now wait minutes for a session, and the ETL pool is both too small per node and too big on quiet nights.

## What you'll do

Choose the default pool, a custom pool's node size and scaling, and session sharing so three teams each get the Spark compute their work needs.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your pool settings give each team what its work needs, as the Spark compute pages describe pools, node sizes, autoscale and high concurrency, checked against a model on this PC.

## What it doesn't prove

- That any pool is created or any session starts: nothing runs here.
- Capacity limits, such as the Spark vCores a capacity allows and the 200-node pool limit.
- The exact conditions under which notebooks share a high concurrency session.
