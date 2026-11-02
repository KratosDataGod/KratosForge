# Domains and data products

In a data mesh, each business domain owns its data and publishes the parts others need as data
products: tables with an owner, a contract (LM-30), documentation and a promise to keep them
working. Fabric domains group workspaces by business area; KratosForge's Data mesh page tracks
products and the requests between domains.

## Dependencies cross boundaries

A product built from another domain's product depends on a team it doesn't manage. Those are the
links that break when something changes upstream, so list them:

```sql
SELECT d.consumer, d.source, s.domain AS source_domain
FROM dependencies AS d
JOIN products AS c ON c.product = d.consumer
JOIN products AS s ON s.product = d.source
WHERE c.domain = 'finance' AND s.domain <> 'finance';
```

Every row is a contract the source domain should know it has, and a team Finance should hear
from before a breaking change.

## Try it

Find Finance's dependencies on other domains.
