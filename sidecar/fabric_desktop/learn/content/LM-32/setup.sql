CREATE TABLE workspaces (workspace VARCHAR, stage VARCHAR);
INSERT INTO workspaces VALUES ('Sales-Prod', 'Prod'), ('Sales-Dev', 'Dev'), ('Finance-Prod', 'Prod');
CREATE TABLE grants (principal VARCHAR, workspace VARCHAR, role VARCHAR);
INSERT INTO grants VALUES
    ('ana', 'Sales-Prod', 'Admin'), ('ana', 'Sales-Dev', 'Admin'), ('ben', 'Sales-Prod', 'Viewer'),
    ('cy', 'Finance-Prod', 'Member'), ('ops-team', 'Finance-Prod', 'Admin'), ('dee', 'Finance-Prod', 'Contributor');
CREATE TABLE platform_admins (principal VARCHAR);
INSERT INTO platform_admins VALUES ('ops-team');
