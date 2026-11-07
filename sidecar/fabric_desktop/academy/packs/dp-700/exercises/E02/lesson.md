# An environment for a notebook: libraries, resources and the default environment

The sales team's notebooks each pip-install what they need at the top, and runs break when a package changes. You're moving everything into one environment, but the draft has the wheel and the CSV swapped and was never published.

## What you'll do

Put public and custom libraries and a small data file in the right parts of an environment, publish it the right way, and make it the workspace default.

## How it runs here

This lab is a model, not Fabric. settings.json holds what each team needs and the settings you choose. Check my work reads it as data; Run lists every setting so you can see what the checks read.

## What this run proves

- Your environment puts each library and file where it belongs, is published in the mode production needs, and is the workspace default, as the environment pages describe them, checked against a model on this PC.

## What it doesn't prove

- That the environment publishes or that the libraries install: nothing runs here.
- Spark properties and compute settings in the environment.
- Your organization's rules on where packages may come from.
