"""How much of the project the Supervisor is shown.

These values are the subject of the user's own experiment (docs/TO-TEST.md). Do not change them.
"""
MAX_CONTEXT_CHARS = 60000
# A task awaiting review needs enough of its run output for the supervisor to
# score completeness/evidence; every other task only needs a short reminder.
MAX_RUN_OUTPUT_PREVIEW_CHARS = 300
MAX_RUN_OUTPUT_REVIEW_CHARS = 8000
MAX_REVIEW_OUTPUT_TOTAL_CHARS = 24000
