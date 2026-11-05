# Environments, Git and deployments

Each environment gets its own workspace: Dev to build, Test to check, Prod for the business.
Changes reach Prod one way: committed to Git, reviewed, then deployed by a deployment pipeline or
CI/CD, never edited in Prod by hand. That keeps Prod reproducible and every change reviewable.

## Know what a deployment changes

Before deploying, compare the two sides. Fabric deployment pipelines show new, different and
identical items; KratosForge's Promote check does the same and flags risks. Underneath it is a
comparison like this:

```sql
SELECT d.item, CASE WHEN p.item IS NULL THEN 'add' ELSE 'update' END AS change
FROM dev_items AS d
LEFT JOIN prod_items AS p ON p.item = d.item
WHERE p.item IS NULL OR p.version <> d.version;
```

Items only in Prod aren't deleted by a deployment; remove them on purpose. Connections and
parameters differ per environment, so deployment rules or variables set them, never the code.

## Try it

List what deploying Dev to Prod would add and update.
