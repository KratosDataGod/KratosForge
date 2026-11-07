# Academy (Preview)

**Academy** is at the bottom of the left rail, above Source control and Settings, where Learn used to
be. It prepares you for a certification, starting with DP-700 (Implementing Data Engineering Solutions
Using Microsoft Fabric), with exercises you do on this PC. Each exercise says how your work is checked
and what a pass does and doesn't prove.

Everything runs on this PC with data generated here. An exercise never reads or writes OneLake, never
starts a Fabric job and never publishes anything to Fabric.

## The tabs

| Tab | What it shows |
| --- | --- |
| Home | Your next exercise, each exam domain's progress, where Learn's lessons and your rank went, and what's recommended next |
| Path | Every objective of the study guide, word for word, by domain: the best evidence you have, its status and its exercises. **Show only what's left** hides the objectives you've proved |
| Practice | The published exercises, and Learn's lessons and labs with your progress kept |
| Assessments | Diagnostics and practice exams, when the Assessment Center arrives |
| Readiness | Six separate measures for each objective: knowledge, guided, independent, troubleshooting, verified on the platform, and retention |
| Portfolio | The exercises you've passed and what each counts for, with a link to your rank, achievements and profile picture in Profile |

## How work is checked

| Label | Meaning |
| --- | --- |
| LOCAL VERIFIED | Your work ran on this PC and passed its checks |
| SIMULATED | Checked against a model of the service, not the service itself |
| FABRIC VERIFIED | Ran in your own Fabric workspace. Not available yet |
| KNOWLEDGE ONLY | Questions; nothing ran |

A pass on this PC never claims to be a pass in Fabric. An objective that only Fabric can show stays
**Practised · needs Fabric** until you pass a FABRIC VERIFIED exercise for it.

## An exercise

Open an exercise from Home, Path or Practice and choose **Start the exercise**. That makes a folder for
it under this PC's KratosForge data (`academy/<certification>/<exercise>`) with the exercise's
generated data and your answer, which is saved as you type and kept after a restart.

- **Run** (Ctrl+Enter) runs your SQL and shows the first rows. Changes are undone after each run.
- **Check my work** runs the exercise's checks on a fresh copy of its data. A pass is recorded in
  Profile for the exercise's objectives, with how many hints you used.
- **Hints** go from a nudge to nearly the answer. A challenge passed with more than one hint, or after
  opening the explanation, doesn't count as independent. Neither does a quiz passed after changing an answer
  that was already checked.
- **…** has **Compare with the expected result** and **Reset the exercise**, which asks first because it
  throws your answer away.

The panel beside the editor has **Task** (the scenario, requirements, how it's checked and the
lesson), **Hints** and **Check** (each check's result, what it proves and doesn't, the explanation once
you've passed, and what was recorded).

## Where your exercise code runs

Your exercise code runs on this PC in its own limited process, with time, memory and output limits, and it can
see only the exercise's folder. On Windows, exercise scripts also run in a Windows AppContainer by default (Delta and PySpark exercises use the limited process only).
To allow that, KratosForge gives its own exercise sandbox read access to the engine's Python. **Settings > Advanced
> Exercise sandbox** shows whether the AppContainer is on and how many folders hold that permission.
**Remove exercise permissions** takes the permission away after asking; it can't run while an exercise is running,
and the next exercise you run adds it back. How the sandbox works is in
[Grading sandbox](academy/grading-sandbox.md).

## Readiness is an estimate

Readiness is KratosForge's own estimate from the evidence on this PC. It isn't Microsoft's score and it
doesn't promise a pass. Results stay tied to the version of the study guide they were earned under;
when the guide changes, earlier results are kept as they were.

Academy uses KratosForge's own exercises and questions, never real exam questions.

The plan behind Academy is in [Academy plan](academy/README.md).
