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
| DP-700 (Microsoft Fabric) | 534, at least 10 for each of the 49 objectives | A Microsoft Learn page |
| Databricks Certified Data Engineer Associate (draft pack) | 165, 5 for each of the 33 objectives | A Databricks documentation page |

## Three ways to start

| Assessment | Questions | Time |
| --- | --- | --- |
| Practice exam | The certification's published length when it has one (Databricks: 45 in 90 minutes), otherwise 50 in 100 minutes; spread evenly over every objective | That time, or untimed |
| Diagnostic | 2 for each objective in the domain you pick, or in all of them | Untimed |
| Objective drill | Every question for the objectives you pick, with the answer and why after each one | Untimed |

The diagnostic screen opens from **Take a diagnostic** on Academy Home or from the diagnostic card in
Assessments. Each choice shows how many questions it asks, about how long that takes at the practice exam's
pace, and the last time you finished it on this PC with how many you got right. Right answers count toward
Knowledge in Readiness only.

Questions are single answer, multiple answers (the question says how many to pick), ordering, or one of
four kinds with several parts:

| Kind | You answer |
| --- | --- |
| Matching | Choose an item for each requirement from its list, or drag the item onto it. An item can be used more than once. |
| Hot area | Choose a value from the list at each numbered blank in a settings pane or snippet. |
| Yes or no | Choose Yes or No for each statement. |
| Code completion | Choose a token from the list at each numbered gap in PySpark or T-SQL code. Each token goes in one gap at most: choosing one already used moves it. |

A question with several parts counts as answered only when every part has an answer.

Some questions belong to a short case study, which stays above the question.

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
is in the question: 1 to 8 choose an option (in single and multiple answer questions), F flags, N goes to the next question, P to the previous
one and R opens the review. Turn the shortcuts off in the panel on the right if they get in the way
of your screen reader. While a list has focus, letters choose in the list instead.

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

Each question is worth one point. A question with several parts earns partial credit: with k of its
n parts right it earns k/n of its point, so 3 of 4 matches right is 0.75 points. A question that isn't
fully answered earns nothing. Totals, results by objective and domain, and the knowledge measure in
Readiness add these points, so they can be fractional (shown to 2 decimal places).

A finished assessment shows the points scored, the result by domain with the domain's published
weight, and your weakest objectives with an objective drill for each. **See answers and why** goes
through each question with your answer and the right one marked in words, why, and the page it's
based on. For a question with several parts, each part is shown right or wrong next to the correct
answer, under a line such as "3 of 4 matches right · 0.75 points". A retake treats a question that was
only partly right as one to ask again.

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
vendor's documentation site, an ordering question stored in its answer order, a multi-part question whose snippet's numbered
blanks (`{{1}}`, `{{2}}`...) don't match its parts, raw HTML, an unused
case study or an objective with fewer than 4 live questions.
