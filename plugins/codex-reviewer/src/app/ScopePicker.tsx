import { useEffect, useId, useRef, useState } from "react";
import type { RepositoryInfo, RepositoryRef, ReviewScope } from "../server/types";
import { Icon } from "./Icon";

export function filterBranchRefs(refs: RepositoryRef[], query: string): RepositoryRef[] {
  const common = ["main", "master", "dev", "develop"];
  const priority = (ref: RepositoryRef) => {
    const name = ref.fullName.startsWith("refs/remotes/") ? ref.name.slice(ref.name.indexOf("/") + 1) : ref.name;
    const index = common.indexOf(name.toLowerCase());
    return index < 0 ? common.length : index;
  };
  const search = query.trim().toLowerCase();
  return refs.filter((ref) => ref.name.toLowerCase().includes(search)).sort((a, b) =>
    priority(a) - priority(b) || (priority(a) < common.length ? Number(a.fullName.startsWith("refs/remotes/")) - Number(b.fullName.startsWith("refs/remotes/")) : 0) || a.name.localeCompare(b.name));
}

export function ScopePicker({ repository, scope, busy, onChange }: {
  repository: RepositoryInfo; scope: ReviewScope; busy: boolean; onChange: (scope: ReviewScope) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const container = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const listId = useId();
  const branches = filterBranchRefs(repository.refs, query);
  const selected = scope.kind === "worktree" ? "worktree" : scope.baseRef;
  const options = [{ value: "worktree", label: "工作区变更" }, ...branches.map((ref) => ({ value: ref.fullName, label: `与 ${ref.name} 比较` }))];
  const current = Math.min(active, options.length - 1);
  const label = scope.kind === "worktree" ? "工作区变更" : `与 ${repository.refs.find((ref) => ref.fullName === scope.baseRef)?.name ?? scope.baseRef} 比较`;

  // 1. 打开后聚焦搜索；点击外部、移走焦点或按 Escape 关闭。
  useEffect(() => {
    if (!open) return;
    const closeOutside = (event: PointerEvent) => {
      if (!container.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  useEffect(() => {
    if (open) document.getElementById(`${listId}-${current}`)?.scrollIntoView({ block: "nearest" });
  }, [open, current, listId, query]);

  const choose = (value: string) => {
    setOpen(false);
    trigger.current?.focus();
    if (value !== selected) onChange(value === "worktree" ? { kind: "worktree" } : { kind: "branch", baseRef: value });
  };
  const show = () => {
    setQuery("");
    const sorted = filterBranchRefs(repository.refs, "");
    setActive(selected === "worktree" ? 0 : Math.max(0, sorted.findIndex((ref) => ref.fullName === selected) + 1));
    setOpen(true);
  };

  return <div ref={container} className="scope-picker" onBlur={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setOpen(false);
  }}>
    <button ref={trigger} type="button" className="scope-trigger" aria-label="比较范围" title={label} disabled={busy}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} onClick={() => open ? setOpen(false) : show()}
      onKeyDown={(event) => { if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); show(); } }}>
      <Icon name="branch" /><span>{label}</span><Icon name="chevron" size={12} />
    </button>
    {open && <div className="scope-popover">
      <input autoFocus className="scope-search" type="search" role="combobox" aria-label="搜索分支" placeholder="搜索分支…"
        aria-expanded="true" aria-controls={listId} aria-autocomplete="list" aria-activedescendant={`${listId}-${current}`} value={query} disabled={busy}
        onChange={(event) => { setQuery(event.target.value); setActive(event.target.value.trim() && filterBranchRefs(repository.refs, event.target.value).length ? 1 : 0); }}
        onKeyDown={(event) => {
          if (event.nativeEvent.isComposing) return;
          if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus(); }
          else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault(); setActive((current + (event.key === "ArrowDown" ? 1 : -1) + options.length) % options.length);
          } else if (event.key === "Enter") { event.preventDefault(); choose(options[current].value); }
        }} />
      <div className="scope-options" role="listbox" id={listId} aria-label="比较范围选项">
        {options.map((option, index) => <button key={option.value} id={`${listId}-${index}`} type="button" role="option" tabIndex={-1}
          aria-selected={option.value === selected} disabled={busy} className={`scope-option${index === current ? " is-active" : ""}`}
          title={option.label} onMouseEnter={() => setActive(index)} onClick={() => choose(option.value)}>
          <span>{option.label}</span>{option.value === selected && <span className="scope-selected" aria-hidden="true">✓</span>}
        </button>)}
      </div>
      {!branches.length && <div className="scope-no-results" role="status">{query.trim() ? "没有匹配的分支" : "暂无可比较分支"}</div>}
    </div>}
  </div>;
}
