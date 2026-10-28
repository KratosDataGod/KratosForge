# Semantic model best practices (Preview)

KratosForge checks a Data mesh semantic model (`SM_<Layer>`) against Best Practice Analyzer (BPA) rules by
running [semantic-link-labs](https://github.com/microsoft/semantic-link-labs) in Fabric. semantic-link-labs
is MIT licensed. It needs Microsoft's semantic-link library, which runs only inside Fabric, so the check runs
there and not on this PC.

The engine is in place. The screens (a Best practices card on **Data mesh > Deploy > Semantic model**, and
model rows in **Optimize > Opportunities**) come next, once the mockup is approved.

## What a check does

1. It finds the model by name in the environment's workspace. The model must already exist, so create or
   update it first.
2. It runs the [risk check](finops/plan.md) for a run in Fabric in that workspace. A blocked result stops
   the check. A warning goes ahead only after the person acknowledges it, and the acknowledgement is kept
   with the others (`risk_acknowledgements`).
3. It publishes the notebook `nb_fabric_desktop_bpa` to the workspace, unless the same notebook is already
   there. It is a Fabric Python notebook whose default lakehouse is the environment's `LH_Meta`.
4. It runs that notebook, with inline installation turned on for the run (`_inlineInstallationEnabled`), since
   a notebook run as a job skips `%pip` otherwise. The notebook installs semantic-link-labs `0.17.1` into its own session, then calls
   `run_model_bpa(..., extended=True, return_dataframe=True)`. `extended` collects the model's VertiPaq
   statistics, which some rules need. The model is read through its XMLA endpoint, so the capacity needs
   XMLA read, which is on by default.
5. The notebook saves the findings to `LH_Meta/Files/_fabric_desktop/results/<request>.json`. The app reads
   them back once the job finishes and keeps the last result for each model on this PC.

**What it writes:** the notebook item (step 3), one result file per check (step 5), and a line in the app's
Fabric runs, so the check shows in Monitor. **What it never changes:** the model, its reports or its data.

**Capacity:** every check runs a notebook on the workspace's capacity, so it uses capacity. It is a Python
notebook, not Spark, to keep that small.

## Rules

The rules are semantic-link-labs' own set: 64 rules in 0.17.1, ported from
[Microsoft's BPA rules](https://github.com/microsoft/Analysis-Services/tree/master/BestPracticeRules).
Findings come back with a severity (Error, Warning or Info), a category, the rule, the object, and what the
rule asks for.

The generated model already follows the rules that KratosForge controls. For example, no column sums on its
own, and each fact has a total measure per numeric column instead. Findings that the generator causes are
fixed by the next **Update model**, not by editing the model in Fabric.

## Engine methods

| Method | What it does | Changes |
| --- | --- | --- |
| `model_bpa_status(env, layer?, name?)` | The model's last findings, a check still running, or one that failed in Fabric | Nothing in Fabric |
| `model_bpa_risk(env, layer?, name?, refresh?)` | The risk check for checking this model in Fabric | Nothing |
| `model_bpa_check(env, layer?, name?, risk_id?)` | Starts a check | Publishes the notebook and starts a Fabric job (capacity) |

## Permissions

There is no new sign-in scope. The notebook runs as the person who starts it, so they need to be able to run
notebooks in the workspace and read the model. See [permissions](permissions.md).
