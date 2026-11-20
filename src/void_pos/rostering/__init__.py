"""Staff rostering, time-clocking and timesheets, with a Deputy sync (venue's own token)."""

from __future__ import annotations

from .deputy import (
    DeputyApi,
    DeputyClient,
    DeputyPullResult,
    DeputyPushResult,
    MatchReport,
    match_employees,
    pull_roster,
    push_timesheets,
    translate_rosters,
)
from .service import (
    approve_timesheet,
    build_timesheet,
    build_timesheets_for_date,
    clock_in,
    clock_out,
    get_timesheet,
    list_clock_entries,
    list_shifts,
    list_timesheets,
    schedule_shift,
)

__all__ = [
    "DeputyApi",
    "DeputyClient",
    "DeputyPullResult",
    "DeputyPushResult",
    "MatchReport",
    "approve_timesheet",
    "build_timesheet",
    "build_timesheets_for_date",
    "clock_in",
    "clock_out",
    "get_timesheet",
    "list_clock_entries",
    "list_shifts",
    "list_timesheets",
    "match_employees",
    "pull_roster",
    "push_timesheets",
    "schedule_shift",
    "translate_rosters",
]
