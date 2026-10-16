import type { FileView } from "../server/types";
import { MonacoView } from "./MonacoDiff";
import { Icon } from "./Icon";
import { FileIcon } from "./FileIcon";

export function DiffViewer({ view, selectedPath, loading, initialChange, onBoundary }: {
  view: FileView | null; selectedPath: string | null; loading: boolean;
  initialChange: "first" | "last"; onBoundary: (direction: -1 | 1) => void;
}) {
  if (loading) return <div className="diff-empty"><span className="loading-indicator" /><span>正在读取文件…</span></div>;
  if (!selectedPath) return <div className="diff-empty"><Icon name="file" size={32} /><span>选择文件查看</span></div>;
  if (!view) return <div className="diff-empty"><span>无法读取此文件</span></div>;
  const diff = view.mode === "diff" ? view.fileDiff : null;
  const path = view.mode === "file" ? view.path : view.fileDiff.newPath ?? view.fileDiff.oldPath ?? selectedPath;
  const supported = view.mode === "file" ? view.supported : view.fileDiff.supported;
  const reason = view.mode === "file" ? view.unsupportedReason : view.fileDiff.unsupportedReason;
  return <section className="diff-panel" aria-label={diff ? "Side-by-side diff" : "Readonly file"}>
    <header className="diff-file-header"><FileIcon path={path} /><span title={path}>{path}</span>
      {diff ? <span className={`file-status status-${diff.status}`}>{{ added: "新增", modified: "已修改", deleted: "已删除", renamed: "已重命名", copied: "已复制" }[diff.status]}</span>
        : <span className="file-status">{view.mode === "file" && view.source === "revision" ? "HEAD" : "工作区"} · 只读</span>}
    </header>
    {!supported ? <div className="diff-empty"><span>{reason}</span></div> : <>
      {diff && <div className="diff-column-labels"><span title={diff.oldPath ?? ""}>原文件{diff.oldPath && diff.oldPath !== diff.newPath ? ` · ${diff.oldPath}` : ""}</span><span title={diff.newPath ?? ""}>修改后</span></div>}
      <MonacoView view={view} initialChange={initialChange} onBoundary={onBoundary} />
    </>}
  </section>;
}
