# Strategy Guide

This guide helps you choose the right allocation strategy and understand how each one behaves.

## Quick Selection Flow

```
Start
  |
  |-- Prefer simplicity / one tracker / few torrents?  --> equal
  |
  |-- Many torrents on a tracker; stronger should get more?  --> weighted
  |
  |-- Multiple trackers; some under-used, others starved?     --> soft

Unsure? Start with equal → if some torrents need more, try weighted → if you leave bandwidth on the table across trackers, enable soft.
```

## Assumptions (examples)

- Upstream link speed: 100 MiB/s (not the limiting factor in examples).
- Catch‑all (default) tracker is last with pattern `.*`.
- In examples, the catch‑all is unlimited (`max_upload_speed: -1`) so unmatched torrents are uncapped.

## Strategy Summaries

- equal (Phase 1)
  - Equal split per tracker across its active torrents; 10 KiB/s per‑torrent floor.
  - Pros: predictable, low churn/API calls; simplest rollout.
  - Cons: can under‑utilize cap when most torrents are weak.

- weighted (Phase 2)
  - Within‑tracker proportional allocation by score (peers ×0.6 + speed ×0.4); bounds: min 10 KiB/s, max 60% of tracker cap.
  - Pros: stronger torrents get more; better within‑tracker efficiency.
  - Cons: no cross‑tracker borrowing; more dynamic than equal.

- soft (Phase 3)
  - Cross‑tracker borrowing of unused capacity; shares weighted by tracker priority and capped per tracker; smoothed effective caps.
  - Pros: highest overall utilization; priorities bias important trackers; smoothing reduces churn; preview endpoint helps inspect changes.
  - Cons: more moving parts; effective caps vary; knobs require tuning.

## Examples (with catch‑all)

### equal
- Tracker T (cap 4 MiB/s), 4 active torrents → each ≈ 1.00 MiB/s.
- Default (unlimited) has 2 unmatched torrents → both set to unlimited (‑1 per‑torrent limit).
- If T has 400 torrents, equal share ≈ 10 KiB/s but floor is 10 KiB/s; many sit at the floor and T might not fully saturate if peers are weak. Torrents on unlimited default remain uncapped.

### weighted
- T cap 6 MiB/s, two torrents:
  - A: 40 peers, 0.8 MiB/s now → score ≈ 0.92.
  - B: 5 peers, 0.2 MiB/s now → score ≈ 0.23.
  - A gets much more than B but within the per‑torrent max (60%); excess redistributed.
- Default (unlimited) has torrent D → D set to unlimited (‑1). Unlimited trackers skip weighting by design.

### soft
- A (4 MiB/s) uses 1 MiB/s → 3 MiB/s leftover pool.
- B (2 MiB/s), priority 10, wants ~3 MiB/s → eligible to borrow.
- Default (unlimited) remains uncapped and does not borrow.
- With `borrow_threshold_ratio=0.9`, `max_borrow_fraction=0.5`:
  - B borrows up to min(pool, 0.5×base_B) = min(3.0, 1.0) = 1.0 MiB/s.
  - Effective cap B = 2.0 + 1.0 = 3.0 MiB/s; A stays 4.0 MiB/s; default remains unlimited.
  - Smoothing tempers sudden swings; small changes under `min_effective_delta` are ignored.

## Soft in 30 seconds (plain‑English)

- Each tracker is a tap with a labelled flow (base cap).
- Unused flow goes to a shared bucket.
- Busy taps borrow from the bucket; priorities decide who gets more.
- Safety rails: borrow caps and smoothing to avoid jerkiness.

## Three quick scenarios (no math)

- One quiet, one busy → busy gets a boost; when quiet wakes up, boost shrinks.
- Two busy, different priorities → both boost; higher priority gets more.
- All busy → no leftovers; behaves like weighted/equal.

## Safety defaults (good starting values)

- borrow_threshold_ratio: 0.9 (only trackers using ≳90% of base try to borrow)
- max_borrow_fraction: 0.5 (no tracker borrows > 50% of its base)
- smoothing_alpha: 0.4 (caps change smoothly)
- min_effective_delta: 0.1 (ignore tiny changes)

## Quick guidance

- Start with `equal` for safety.
- If some torrents need more within a tracker, try `weighted`.
- If you leave bandwidth on the table across trackers, enable `soft` (tune knobs).
- Use `GET /preview/next-cycle` before switching strategies; increase `rollout_percentage` gradually.

