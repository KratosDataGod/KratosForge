# Put a workspace under Git: commit on a branch and resolve a conflict in a pipeline definition

The Ingest workspace is connected to Git, so each item is a folder of text files in the repository. You're making the copy activity retry 3 times on feature/retry. Meanwhile a teammate gave the same activity a two-hour timeout on main, on the very next line of the same file. Before your branch can go back, it has to carry both changes.

## What you'll do

Work on a branch of a workspace's Git repository: commit a change to a pipeline's definition, merge main in, and resolve the conflict so both changes survive.

## How it runs here

Your answer is a Git session. Each git line runs real Git on this PC in a repository made for this exercise, and a write block replaces a file's whole content. Only everyday commands run (status, log, diff, switch, add, commit, merge and similar); nothing reaches a remote. In Fabric the same files are what the Source control pane commits for you.

## What this run proves

- Your Git session, run with Git on this PC, leaves feature/retry with your commit and main merged in, the merge finished, and a pipeline definition that keeps both changes with no conflict markers. main is untouched.

## What it doesn't prove

- Fabric's Source control pane: connecting a workspace, committing from it, or updating it from Git. Here you use Git directly on the files Fabric would write.
- That Fabric accepts the definition: only the fields this exercise changes are checked, and the file is a cut-down pipeline definition.
- Pull requests and branch policies in Azure DevOps or GitHub.
