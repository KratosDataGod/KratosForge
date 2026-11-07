import collections, glob, json, os
agg = collections.defaultdict(lambda: [0.0, 0])
for f in glob.glob(os.path.join(os.environ["RUNNER_TEMP"], "acprof-*.json")):
    for total, n, k in json.load(open(f)):
        agg[k][0] += total; agg[k][1] += n
for k, (t, n) in sorted(agg.items(), key=lambda kv: -kv[1][0])[:40]:
    print(f"{t:9.1f}s {n:6d}x {t / n * 1000:9.1f} ms each  {k}")
