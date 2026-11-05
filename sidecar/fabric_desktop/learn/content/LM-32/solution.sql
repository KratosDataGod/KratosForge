SELECT g.principal, g.workspace, g.role
FROM grants AS g
JOIN workspaces AS w ON w.workspace = g.workspace
WHERE w.stage = 'Prod'
  AND g.role IN ('Admin', 'Member', 'Contributor')
  AND NOT EXISTS (SELECT 1 FROM platform_admins AS a WHERE a.principal = g.principal)
ORDER BY g.principal, g.workspace;
