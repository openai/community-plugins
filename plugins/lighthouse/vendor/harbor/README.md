# Harbor references

These files are copied unchanged from
[Harbor v0.23.0](https://github.com/harbor-framework/harbor/tree/1e5c5c6db929a10a140d05e606882c671ae20729),
matching the benchmark runtime pin. The full commit is
`1e5c5c6db929a10a140d05e606882c671ae20729`.
They are reference material, not installed Lighthouse skills or a Harbor runtime.

- [Create task](skills/create-task/SKILL.md): scaffold and build a Harbor task.
- [Harbor exec](skills/harbor-exec/SKILL.md): compile inputs into map-reduce jobs.
- [Exec examples](skills/harbor-exec/references/examples.md): commands and configs.
- [RewardKit](skills/rewardkit/SKILL.md): declare deterministic and LLM judge criteria.

Harbor's [Apache-2.0 license](LICENSE) is included. Keep upstream files unchanged;
put Lighthouse-specific guidance in Lighthouse's own skills. When updating these
references, record the new commit and check compatibility with the chosen Harbor
version. The pinned RewardKit skill's `0.1.*` installation example disagrees with
the same commit's package version, `0.2.0`. Lighthouse's
[quality recipe](../../skills/lh-create-task/references/harbor-code-quality.md)
pins the source version explicitly. Other upstream skills and docs mentioned by
these files are not bundled.
