# Least-privilege workspace roles for six people

The Sales workspace grew without a plan, and an audit asks for least privilege. You have six people, a line on what each one does, and one person who left last week.

## What you'll do

Give six people the least workspace role (Admin, Member, Contributor or Viewer) that covers what each one does, and remove someone who has left.

## How it runs here

This lab is a model, not a workspace. settings.json holds what each person needs and the setting you choose for them. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your role for each person is the least of Admin, Member, Contributor and Viewer that covers their work, as the Fabric role table describes it, and the person who left has none, checked against a model on this PC.

## What it doesn't prove

- That anyone gets these roles in Fabric: nothing is set in a workspace here.
- Item permissions, sharing and OneLake security roles, which can give someone access without a workspace role.
- Access to shortcut destinations, which needs permissions on the target as well.
