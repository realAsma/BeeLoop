---
name: timers
description: Schedule, inspect, or cancel wake timers for this BeeLoop agent.
---

# BeeLoop timers

Use `timer_create(after="45m", message="Check the build")` for a one-shot wake.
Use `timer_create(every="1h", duration="7d", message="Review progress")` for a
bounded recurring wake; omit `duration` to repeat until cancelled.

Durations are positive integers ending in `s`, `m`, `h`, or `d`. The returned
timer ID identifies the schedule. Use `timer_list()` to inspect current timers
and `timer_cancel(timer_id=...)` to remove one. All three tools are bound to the
current agent.
