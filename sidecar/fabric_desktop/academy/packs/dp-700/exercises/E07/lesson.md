# Feature workspaces: branch out, sync, and update from Git

The Ingest workspace is connected to main. Ana and Ben each branched out to a workspace of their own. Ana's dedupe change is approved; Ben (you) still has to make the copy retry 3 times. A hotfix landed on main this morning, and the team's branch policy only completes a PR whose branch has main's latest commit.

## What you'll do

Work the way branched workspaces do in Git: a feature branch from main's latest commit, a commit on it, an update from main after another PR lands, and your PR completed into main.

## How it runs here

Your answer is a Git session. Each git line runs real Git on this PC in a repository made for this exercise, and a write block replaces a file's whole content. Nothing reaches a remote: merging into main stands in for completing a pull request, and merging main into your branch stands in for updating it. In Fabric, a branched workspace commits to and updates from its own branch through the Source control pane.

## Before you start

- E06

## What this run proves

- Your Git session, run with Git on this PC, leaves your change on feature/ben-retry, that branch updated with main after Ana's PR, and main holding both features and the hotfix, with no merge left unfinished.

## What it doesn't prove

- Fabric's Branches tab: Branch out to another workspace, the workspace it creates, or which items it copies.
- Fabric's Update all, the Changes and Updates counts, or that commits are disabled while updates are waiting.
- Pull requests and branch policies in Azure DevOps or GitHub: here you complete them as merges yourself.
