"""Search-API query plan: two career tracks share the per-run budget, phrases rotate between runs."""

from __future__ import annotations

import re
from typing import Any

YEAR = re.compile(r"\s*\b20(26|27)\b")


def _yearless(query: str) -> str | None:
    stripped = re.sub(r"\s+", " ", YEAR.sub("", query.replace('"summer 2027"', '"summer"'))).strip()
    return stripped if stripped and stripped != query else None


def track_queries(track: dict[str, Any], yearless: bool) -> list[tuple[str, str]]:
    """(kind, phrase) pairs for one track: posts and jobs interleaved, job phrases also without the year."""
    posts = [("post", q) for q in track.get("post_queries", [])]
    jobs = []
    for q in track.get("job_queries", []):
        jobs.append(("job", q))
        if yearless and (v := _yearless(q)):
            jobs.append(("job", v))
    out = []
    for i in range(max(len(posts), len(jobs))):
        out += [x[i] for x in (jobs, posts) if i < len(x)]
    return out


def plan(discovery: dict[str, Any], rotation: int = 0, mode: str | None = None) -> list[tuple[str, str]]:
    """The queries to run now, as (kind, phrase). See `plan_by_track`."""
    return [(kind, q) for _, kind, q in plan_by_track(discovery, rotation, mode)]


def _pick(pool: list[tuple[str, str]], count: int, rotation: int, job_share: float | None) -> list[tuple[str, str]]:
    """`count` phrases from a rotating window; with a job share, jobs and posts rotate separately."""
    if not pool or count <= 0:
        return []
    if job_share is None:
        start = (rotation * count) % len(pool)
        return [pool[(start + i) % len(pool)] for i in range(min(count, len(pool)))]
    jobs = [x for x in pool if x[0] == "job"]
    posts = [x for x in pool if x[0] == "post"]
    n_jobs = min(len(jobs), round(count * job_share))
    n_posts = min(len(posts), count - n_jobs)
    n_jobs = min(len(jobs), count - n_posts)          # give unused post slots back to jobs
    picked_jobs, picked_posts = _pick(jobs, n_jobs, rotation, None), _pick(posts, n_posts, rotation, None)
    out = []
    for i in range(max(len(picked_jobs), len(picked_posts))):     # interleave, jobs first
        out += [x[i] for x in (picked_jobs, picked_posts) if i < len(x)]
    return out


def plan_by_track(discovery: dict[str, Any], rotation: int = 0, mode: str | None = None) -> list[tuple[str, str, str]]:
    """(track, kind, phrase) to run now. Legacy configs (flat post_queries/job_queries) stay a single track."""
    budget = int(discovery.get("max_queries_per_run", 15))
    yearless = bool(discovery.get("add_yearless_variants", False))
    tracks = discovery.get("tracks") or {"business_tech": {"post_queries": discovery.get("post_queries", []),
                                                           "job_queries": discovery.get("job_queries", [])}}
    alloc = discovery.get("allocation") or {name: 1.0 for name in tracks}
    total_w = sum(float(alloc.get(n, 0)) for n in tracks) or 1.0
    pools = {n: track_queries(t, yearless) for n, t in tracks.items()}
    names = list(tracks)
    exact = {n: budget * float(alloc.get(n, 0)) / total_w for n in names}
    shares = {n: min(len(pools[n]), int(exact[n])) for n in names}          # floors first
    # Remaining slots by largest fractional remainder; ties rotate between tracks from run to run.
    order = sorted(names, key=lambda n: (-(exact[n] - int(exact[n])), (names.index(n) - rotation) % len(names)))
    spare = budget - sum(shares.values())
    while spare > 0:
        grew = False
        for n in order:                                                     # an exhausted track lends its slots
            if spare and shares[n] < len(pools[n]):
                shares[n] += 1
                spare -= 1
                grew = True
        if not grew:
            break
    picked: dict[str, list[tuple[str, str]]] = {}
    for n, pool in pools.items():
        if not pool or not shares[n]:
            picked[n] = []
            continue
        share = (discovery.get("job_share_by_mode") or {}).get(mode) if mode else None
        picked[n] = [(n, *x) for x in _pick(pool, shares[n], rotation, share)]
    out = []                                       # alternate tracks so a cut-off run still covers both
    lead = names[rotation % len(names):] + names[:rotation % len(names)] if names else []
    for i in range(max((len(v) for v in picked.values()), default=0)):
        out += [picked[n][i] for n in lead if i < len(picked[n])]
    return out[:budget]
