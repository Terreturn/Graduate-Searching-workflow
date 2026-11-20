# Changelog

## v1.1.0 · 2026-11-20

Labeled the school and advisor screening workflow v1.1.0 in both READMEs. [Version commit](https://github.com/Terreturn/Graduate-Searching-workflow/commit/f285915)

The four-turn live acceptance test on 2026-11-20 used the same UBC goal throughout.

| Turn | What the applicant requested | Branches started | Result |
| --- | --- | --- | --- |
| 1 | Find the Statistics MSc at UBC Vancouver and its official page. | School only | Saved the program and campus. |
| 2 | Find Paul Gustafson's research interests and link him to the MSc. | Advisor only | Reused the saved program and verified the advisor's link to its thesis pathway. |
| 3 | Require a thesis option and public evidence of graduate thesis supervision. | School and advisor, in parallel | The worker intervals overlapped. Both conditions were verified for the same pair. |
| 4 | Show only the first verified pair. | Neither | Read the same pair and sources from SQLite with **zero web calls**. |

**Release checks.** All 56 offline regression tests passed. All four live acceptance turns passed, including a recovery that reused earlier evidence. Reopening SQLite reproduced the same pair, sources and table for turns 3 and 4.

## v1.0.0 · 2026-11-20

Added advisor screening and parallel school and advisor searches driven by per-turn requirement changes. SQLite stores verified advisor–program links, training pathways and sources for later turns. The advisor catalog gained scholarly indexes, conference proceedings, project registers and thesis repositories. [Feature commit](https://github.com/Terreturn/Graduate-Searching-workflow/commit/4d7533a)

## 2026-11-17 · Screening requirements and evidence checks

An unresolved hard requirement stays with its goal, degree and country until the applicant answers or withdraws it. Each turn saves the questions that applied at that time. For example, a master's tuition limit of 40,000 with no currency or billing period allows discovery to continue while the affected master's results await clarification. Eligible PhD results can still appear.

For country-specific rules, both researchers and the auditor verify the delivery campus's country. The table renders tuition, stipend amounts and deadlines from their agreed values and links to the pages used for the displayed facts.

All 42 offline regression tests passed. A three-turn live intake test also confirmed that a master's tuition cap survives an unrelated preference change and takes effect once the applicant supplies the missing currency and billing period.
