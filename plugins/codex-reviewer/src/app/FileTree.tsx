import { useEffect, useMemo, useRef, useState } from "react";
import type { ChangedFile, TreeEntry } from "../server/types";
import { Icon } from "./Icon";
import { FileIcon } from "./FileIcon";

type TreeNode = { path: string; name: string; kind: TreeEntry["kind"]; changed: boolean; children: TreeNode[] };
export function buildTree(entries: TreeEntry[]): TreeNode[] {
  const roots: TreeNode[] = [];
  const index = new Map<string, TreeNode>();
  for (const entry of entries) {
    let parent: TreeNode | undefined;
    let current = "";
    const parts = entry.path.split("/");
    for (const [position, name] of parts.entries()) {
      current = current ? `${current}/${name}` : name;
      let node = index.get(current);
      if (!node) {
        node = { path: current, name, kind: "directory", changed: false, children: [] };
        index.set(current, node);
        (parent ? parent.children : roots).push(node);
      }
      if (position === parts.length - 1) Object.assign(node, { kind: entry.kind, changed: node.changed || entry.changed });
      parent = node;
    }
  }
  const finish = (nodes: TreeNode[]): boolean => {
    for (const node of nodes) node.changed = finish(node.children) || node.changed;
    nodes.sort((left, right) => Number(right.kind === "directory") - Number(left.kind === "directory") || left.name.localeCompare(right.name));
    return nodes.some((node) => node.changed);
  };
  finish(roots);
  return roots;
}

export function flattenTree(nodes: TreeNode[], expanded: Set<string>, depth = 0): Array<TreeNode & { depth: number }> {
  const rows: Array<TreeNode & { depth: number }> = [];
  for (const node of nodes) {
    rows.push({ ...node, depth });
    if (node.kind === "directory" && expanded.has(node.path)) rows.push(...flattenTree(node.children, expanded, depth + 1));
  }
  return rows;
}

export function orderedChangedPaths(files: ChangedFile[]): string[] {
  const tree = buildTree(files.map((file) => ({ path: (file.newPath ?? file.oldPath)!, kind: "file", changed: true })));
  const paths: string[] = [];
  const visit = (nodes: TreeNode[]) => nodes.forEach((node) => {
    if (node.kind === "directory") visit(node.children);
    else paths.push(node.path);
  });
  visit(tree);
  return paths;
}

export function FileTree({ entries, changedFiles = [], selectedPath, onSelect }: {
  entries: TreeEntry[]; changedFiles?: ChangedFile[]; selectedPath: string | null;
  onSelect: (path: string, kind: TreeEntry["kind"]) => void;
}) {
  const tree = useMemo(() => buildTree(entries), [entries]);
  const [expanded, setExpanded] = useState(new Set<string>());
  const [viewport, setViewport] = useState({ top: 0, height: 600 });
  const container = useRef<HTMLDivElement>(null);
  useEffect(() => {
    setExpanded((previous) => {
      const next = new Set(previous);
      const visit = (nodes: TreeNode[]) => nodes.forEach((node) => {
        if (node.kind === "directory" && node.changed) next.add(node.path);
        visit(node.children);
      });
      visit(tree);
      return next;
    });
  }, [tree]);
  useEffect(() => {
    if (!container.current) return;
    const observer = new ResizeObserver(([entry]) => setViewport((previous) => ({ ...previous, height: entry.contentRect.height })));
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  const rows = useMemo(() => flattenTree(tree, expanded), [tree, expanded]);
  const statuses = useMemo(() => new Map(changedFiles.flatMap((file) => [file.oldPath, file.newPath].filter((item): item is string => item !== null).map((item) => [item, file.status] as const))), [changedFiles]);
  const rowHeight = 26;
  const start = Math.max(0, Math.floor(viewport.top / rowHeight) - 8);
  const end = Math.min(rows.length, Math.ceil((viewport.top + viewport.height) / rowHeight) + 8);
  return <div ref={container} className="file-tree" role="tree" aria-label="Repository files" onScroll={(event) => {
    const top = event.currentTarget.scrollTop;
    setViewport((previous) => ({ ...previous, top }));
  }}>
    <div className="tree-scroll-content" style={{ height: rows.length * rowHeight }}>
      {rows.slice(start, end).map((node, position) => {
        const directory = node.kind === "directory";
        const status = statuses.get(node.path);
        return <button key={node.path} type="button" role="treeitem" aria-level={node.depth + 1} aria-selected={selectedPath === node.path}
          aria-expanded={directory ? expanded.has(node.path) : undefined} title={node.path}
          className={`tree-row${node.changed ? " is-changed" : ""}${selectedPath === node.path ? " is-selected" : ""} status-${status ?? "unchanged"}`}
          style={{ top: (start + position) * rowHeight, paddingLeft: 8 + node.depth * 14 }}
          onClick={() => directory ? setExpanded((previous) => { const next = new Set(previous); next.has(node.path) ? next.delete(node.path) : next.add(node.path); return next; }) : onSelect(node.path, node.kind)}>
          <span className={`tree-caret${expanded.has(node.path) ? " is-expanded" : ""}`}>{directory && <Icon name="chevron" size={12} />}</span>
          {directory ? <Icon name="folder" /> : <FileIcon path={node.path} />}
          <span className="tree-name">{node.name}</span>
          {status && <span className="tree-status">{{ added: "A", modified: "M", deleted: "D", renamed: "R", copied: "C" }[status]}</span>}
          {directory && node.changed && <span className="tree-dot" />}
        </button>;
      })}
    </div>
  </div>;
}
