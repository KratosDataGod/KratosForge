# Assessments

Academy > Assessments is where you test what you know with original practice questions. It is in
Preview.

!!! note "Not the vendor's exam"
    Every question was written for KratosForge. None of them are Microsoft or Databricks exam
    questions, the screen is not either vendor's exam interface, and a result is not the vendor's
    score, a scaled score or a prediction of passing. It shows which objectives your answers were
    strong or weak on.

## Certifications

| Certification | Questions | Each question is based on |
| --- | --- | --- |
| DP-700 (Microsoft Fabric) | 498, at least 10 for each of the 49 objectives | A Microsoft Learn page |
| Databricks Certified Data Engineer Associate (draft pack) | 165, 5 for each of the 33 objectives | A Databricks documentation page |

## Three ways to start

| Assessment | Questions | Time |
| --- | --- | --- |
| Practice exam | The certification's published length when it has one (Databricks: 45 in 90 minutes), otherwise 50 in 100 minutes; spread evenly over every objective | That time, or untimed |
| Domain diagnostic | 2 for each objective in the domain you pick | Untimed |
| Objective drill | Every question for the objectives you pick, with the answer and why after each one | Untimed |

Questions are single answer, multiple answers (the question says how many to pick) or ordering.
Some belong to a short case study, which stays above the question.

Every attempt is a new random draw. Within each objective, a practice exam or diagnostic asks the questions
you haven't seen in a finished exam on this PC first, then the ones you last got wrong, then the ones you
last got right, so retaking it keeps showing new material until you've seen the whole bank.

## While you answer

- **Flag for review** marks a question to come back to. Flags never change the score.
- The grid on the right goes to any question and says whether it's answered, flagged or current.
- **Review and finish** lists flagged and unanswered questions before you finish. Unanswered
  questions count as wrong, and answers can't change after you finish.
- In a drill, **Check answer** shows whether you were right, the right answer, why and the page the
  question is based on. A checked answer can't change.

## Keyboard

Everything works with Tab, Space and Enter. With **Keyboard shortcuts** on (the default), while focus
is in the question: 1 to 8 choose an option, F flags, N goes to the next question, P to the previous
one and R opens the review. Turn the shortcuts off in the panel on the right if they get in the way
of your screen reader.

In an ordering question, move a step with its ↑ and ↓ buttons, or press Alt+↑ or Alt+↓ while one of
its buttons has focus. Focus stays with the step and the new position is announced. If the steps
are already in the order you want, choose **Answer with this order**.

## The clock

Time counts only while the exam is on screen. Pause stops it; hiding the window, leaving the tab or
closing the app stops it too. The clock never counts more than a minute for any gap, so an exam
left open overnight doesn't use up its time. **Resume** in Unfinished starts it again from where it
stopped. When a timed exam's time is used, it takes no more answers and goes to Review, where you
finish it.

## Results

A finished assessment shows the number right, the result by domain with the domain's published
weight, and your weakest objectives with an objective drill for each. **See answers and why** goes
through each question with your answer and the right one marked in words, why, and the page it's
based on.

A result is scored once, when you finish, and saved on this PC with the certification version and
the version of each question it used. It is never recalculated: if a question is corrected later,
the review says so and your result stays as it was scored.

## Where things are kept

Assessments are files on this PC under KratosForge's data folder (`academy/exams`). Nothing is read
from or written to OneLake, no Fabric job starts and nothing is sent anywhere.

## For content authors

Questions live in `sidecar/fabric_desktop/academy/packs/<pack>/questions/<objective>.json`, one file
per exam objective, with case studies in `cases.json`. Each question cites the documentation page
it's based on. The pack's checks (`exam.problems`) refuse a question without a source on the
vendor's documentation site, an ordering question stored in its answer order, raw HTML, an unused
case study or an objective with fewer than 4 live questions.
