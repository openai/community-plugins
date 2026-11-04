# Leaderboard

No learner results yet. No scores, tokens, cost or duration have been measured.

After running qualified tasks, generate this page from saved native Harbor results
using the helper provided by `lh-run-bench`. Retain the exact regeneration command
with each experiment so the table can be rebuilt without Lighthouse installed.

Show per-task outcomes and an aggregate for the same benchmark version and model.
For code-changing tasks show functional pass, quality pass and overall benchmark
pass together. Overall passes require both gates; count verified passes over all
functionally measured submissions and show confirmed failures, pending reviews,
missing quality and unavailable functional attempts separately.
Include input/output tokens, cost, learner/trial duration and evidence links.
Keep unknown values unavailable, separate infrastructure failures from valid task
failures, and retain failed attempts in total resource use.

Native job outputs live under `jobs/` and are ignored by Git. Preserve them locally
or supply an explicitly authorized evidence handoff; an ignored path is not a
portable link for another reader.
