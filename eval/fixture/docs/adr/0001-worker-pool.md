# ADR 0001 — worker pool for parallel uploads

Date: 2025-11-02 · Status: accepted

We will parallelize uploads with a fixed worker pool, exposed as `--workers`.
A queue per worker keeps ordering deterministic within a release, and a pool
avoids the fork-per-file overhead the prototype showed.
