export type DiffLine = { kind: "add" | "del" | "ctx" | "gap"; text: string; oldNo?: number; newNo?: number };

/** Line diff (LCS) with unchanged regions collapsed to `context` lines around each change. */
export function lineDiff(before: string, after: string, context = 3): { lines: DiffLine[]; added: number; removed: number } {
  const a = before.split("\n"), b = after.split("\n");
  if (a.length * b.length > 4_000_000) {
    return { lines: b.map((text, i) => ({ kind: "ctx" as const, text, newNo: i + 1 })), added: 0, removed: 0 };
  }
  const width = b.length + 1;
  const table = new Uint32Array((a.length + 1) * width);
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i * width + j] = a[i] === b[j] ? table[(i + 1) * width + j + 1] + 1 : Math.max(table[(i + 1) * width + j], table[i * width + j + 1]);
    }
  }
  const full: DiffLine[] = [];
  let i = 0, j = 0, added = 0, removed = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) { full.push({ kind: "ctx", text: a[i], oldNo: i + 1, newNo: j + 1 }); i++; j++; }
    else if (i < a.length && (j >= b.length || table[(i + 1) * width + j] >= table[i * width + j + 1])) { full.push({ kind: "del", text: a[i], oldNo: i + 1 }); i++; removed++; }
    else { full.push({ kind: "add", text: b[j], newNo: j + 1 }); j++; added++; }
  }
  const keep = new Array<boolean>(full.length).fill(false);
  full.forEach((line, index) => {
    if (line.kind !== "ctx") for (let k = Math.max(0, index - context); k <= Math.min(full.length - 1, index + context); k++) keep[k] = true;
  });
  const lines: DiffLine[] = [];
  let skipped = 0;
  full.forEach((line, index) => {
    if (keep[index]) {
      if (skipped) { lines.push({ kind: "gap", text: `${skipped} unchanged line${skipped === 1 ? "" : "s"}` }); skipped = 0; }
      lines.push(line);
    } else skipped++;
  });
  if (skipped && lines.length) lines.push({ kind: "gap", text: `${skipped} unchanged line${skipped === 1 ? "" : "s"}` });
  return { lines, added, removed };
}
