# Things to test (open)

## Supervisor: how much of a web-research report should it see?

**Status:** not started. Neither variant below exists in the code yet; each needs a small build before it can be tested.

### Why this matters

When the Research Supervisor reviews a finished task it scores completeness and evidence, so what it can *see* of the research limits how good that review can be.

Measured on 8 of the real stored deep-research reports (Oct 2026):

| | Range | Typical |
|---|---|---|
| Report length | 28k - 87k characters | ~52k |
| Citations | 31 - 111 | - |
| Unique pages cited | 7 - 19 | ~12 |

Today the supervisor sees at most **8,000 characters** of a task's output: a deduplicated source list (~1.5k) followed by the *start* of the report (~6.4k). That is about **12%** of a typical report. At most 24,000 characters are shown across all tasks awaiting review in one cycle, inside an overall 60,000-character context cap.

Where the limits live:
- `MAX_RUN_OUTPUT_REVIEW_CHARS` = 8000 (per task awaiting review, in `services/review_limits.py`)
- `MAX_REVIEW_OUTPUT_TOTAL_CHARS` = 24000 (all tasks awaiting review, per cycle, in `services/review_limits.py`)
- `MAX_CONTEXT_CHARS` = 60000 (whole context, in `services/review_limits.py`)
- `MAX_WEB_SOURCES_LISTED` = 20 (now in `services/research_execution_service.py`, value unchanged)

### Planned test (in this order)

1. **Summaries first.** Have a model summarise each finished report once, store the summary, and show the supervisor that summary instead of the clipped start of the report.
2. **Then the full report.** Raise the limits so the supervisor sees the whole report. Rough cost: 87k characters is roughly 22k tokens per report, per cycle, and several reports awaiting review multiply that.

### What to compare

- Are the completeness / evidence scores and the outcome (`COMPLETE` vs `FOLLOW_UP_REQUIRED`) sensible for the same finished runs under each setting?
- Does the supervisor claim things that were not in what it was shown (the risk when it sees only ~12%)?
- Token cost and time per supervisor cycle.

Suggested method: use the same project and the same finished runs, run the supervisor's review under each setting, and compare its decisions and stated reasons side by side.
