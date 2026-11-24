# The least workspace role for four groups and two apps

The Claims workspace gives roles to groups and apps, never to single people. A new platform team wants Git and a workspace identity, an orchestrator app starts notebook runs, and a monitoring app only reads their status. The security review wants each one on the least role that works.

## What you'll do

Give four groups and two service principals the least workspace role that covers their tasks, and work out what a person in two groups and a person in a nested group end up with.

## How it runs here

This lab is a model, not a workspace. settings.json holds what each group or app needs and the role you choose for it. Check my work reads it as data. Run lists every setting so you can see what the checks read.

## What this run proves

- Your role for each group and service principal is the least of Admin, Member, Contributor and Viewer that covers its tasks, as the workspace roles table and its notes on groups and the Job Scheduler API describe them, and your answers on overlapping and nested groups follow the same page, checked against a model on this PC.

## What it doesn't prove

- That anyone gets a role in Fabric. Nothing is set in a workspace here.
- Item permissions, sharing and OneLake security roles, which can give access without a workspace role.
- Gateway permissions and permissions on shortcut targets, which are managed outside the workspace roles.
- How a service principal is registered in Microsoft Entra ID and allowed to call Fabric APIs.
