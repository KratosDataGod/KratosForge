# Least privilege in workspaces

Least privilege means each person can do what their job needs and no more. In Fabric the
workspace role decides most of it:

| Role | Can |
| --- | --- |
| Viewer | Read content, and data through the SQL endpoint where allowed |
| Contributor | Also create, edit and delete items, and run them |
| Member | Also share items and add Contributors and Viewers |
| Admin | Also manage the workspace and every role |

## Keep Prod narrow

In Prod, people read; deployments and a small platform team write. Anyone else with Contributor
or above on a Prod workspace can change what reports show without review. Grant roles to Entra
groups, not people, so access follows the job and leaves with it, and review the list regularly.

## Try it

Find grants that let someone outside the platform team change Prod.
