import type { Gate, Rate } from "./contracts.ts";

type Child = Node | string | null | undefined;

export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  for (const child of children) {
    if (child !== null && child !== undefined) node.append(child);
  }
  return node;
}

export function button(label: string, className: string, onClick: () => void): HTMLButtonElement {
  const item = el("button", className, label);
  item.type = "button";
  item.addEventListener("click", onClick);
  return item;
}

export function labelFor(gate: Gate): string {
  switch (gate) {
    case "pass": return "Pass";
    case "fail": return "Fail";
    case "review_required": return "Needs review";
    case "unmeasured": return "Unmeasured";
    case "unknown": return "Unknown";
  }
}

export function badge(gate: Gate): HTMLElement {
  const marker = el("span", "badge__dot");
  marker.setAttribute("aria-hidden", "true");
  return el("span", `badge badge--${gate}`, marker, labelFor(gate));
}

export function rateValue(rate: Rate): string {
  if (rate.denominator === 0) return "—";
  return `${Math.round((rate.numerator / rate.denominator) * 100)}%`;
}

export function rateDetail(rate: Rate): string {
  return `${rate.numerator} of ${rate.denominator}`;
}

export function humanize(value: string): string {
  return value.replaceAll(/[-_]+/g, " ").replace(/(^|\s)\w/g, (letter) => letter.toUpperCase());
}

export function empty(title: string, detail: string): HTMLElement {
  return el("div", "empty", el("p", "empty__title", title), el("p", "muted", detail));
}

export function table(headers: string[], rows: HTMLElement[][]): HTMLElement {
  const tbl = el("table", "data-table");
  const head = el("tr");
  for (const header of headers) {
    const cell = el("th", undefined, header);
    cell.scope = "col";
    head.append(cell);
  }
  tbl.append(el("thead", undefined, head));
  const body = el("tbody");
  for (const values of rows) {
    const row = el("tr");
    for (const value of values) row.append(el("td", undefined, value));
    body.append(row);
  }
  tbl.append(body);
  return el("div", "table-scroll", tbl);
}

function inline(parent: HTMLElement, text: string): void {
  for (const part of text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g)) {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      parent.append(el("code", undefined, part.slice(1, -1)));
    } else if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      parent.append(el("strong", undefined, part.slice(2, -2)));
    } else {
      parent.append(part);
    }
  }
}

function isTableDivider(line: string): boolean {
  const stripped = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return stripped.split("|").every((cell) => /^\s*:?-{3,}:?\s*$/.test(cell));
}

function markdownRow(line: string): string[] {
  return line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

export function markdown(source: string): HTMLElement {
  const output = el("div", "markdown");
  const lines = source.replaceAll("\r\n", "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] ?? "";
    const next = lines[index + 1];
    if (!line.trim()) {
      index += 1;
    } else if (line.trimStart().startsWith("```")) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? "").trimStart().startsWith("```")) {
        code.push(lines[index] ?? "");
        index += 1;
      }
      output.append(el("pre", undefined, el("code", undefined, code.join("\n"))));
      if (index < lines.length) index += 1;
    } else if (line.includes("|") && next !== undefined && isTableDivider(next)) {
      const header = markdownRow(line);
      index += 2;
      const body: HTMLElement[][] = [];
      while (index < lines.length && (lines[index] ?? "").includes("|") && (lines[index] ?? "").trim()) {
        body.push(markdownRow(lines[index] ?? "").map((value) => {
          const cell = el("span");
          inline(cell, value);
          return cell;
        }));
        index += 1;
      }
      output.append(table(header, body));
    } else {
      const heading = /^(#{1,6})\s+(.+)$/.exec(line);
      const list = /^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$/.exec(line);
      if (heading?.[1] && heading[2]) {
        const node = heading[1].length <= 2 ? el("h3") : el("h4");
        inline(node, heading[2]);
        output.append(node);
      } else if (list?.[1]) {
        const node = el("ul");
        while (index < lines.length) {
          const match = /^\s*(?:[-*+]\s+|\d+[.)]\s+)(.+)$/.exec(lines[index] ?? "");
          if (!match?.[1]) break;
          const item = el("li");
          inline(item, match[1]);
          node.append(item);
          index += 1;
        }
        output.append(node);
        continue;
      } else {
        const node = el("p");
        inline(node, line);
        output.append(node);
      }
      index += 1;
    }
  }
  return output;
}
