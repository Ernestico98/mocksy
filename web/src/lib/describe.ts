// Turns Excalidraw elements into stable, human-readable lines so the
// interviewer can follow the whiteboard without looking at an image.

interface El {
  id: string;
  type: string;
  isDeleted?: boolean;
  x: number;
  y: number;
  width: number;
  height: number;
  text?: string;
  containerId?: string | null;
  startBinding?: { elementId: string } | null;
  endBinding?: { elementId: string } | null;
}

const SHAPES: Record<string, string> = { rectangle: "box", ellipse: "circle", diamond: "diamond", frame: "frame" };

const clean = (t?: string) => (t || "").replace(/\s+/g, " ").trim().slice(0, 80);

const within = (a: El, b: El) =>
  a.x >= b.x && a.y >= b.y && a.x + a.width <= b.x + b.width && a.y + a.height <= b.y + b.height;

export function describe(elements: readonly unknown[]): string[] {
  const alive = (elements as El[]).filter((e) => !e.isDeleted);
  const byId = new Map(alive.map((e) => [e.id, e]));
  const boundText = new Map(
    alive.filter((e) => e.type === "text" && e.containerId).map((e) => [e.containerId as string, e.text]),
  );
  const shapes = alive.filter((e) => SHAPES[e.type]);

  const name = (e?: El) => {
    if (!e) return null;
    if (e.type === "text") return `text "${clean(e.text)}"`;
    const label = boundText.get(e.id);
    const kind = SHAPES[e.type] || e.type;
    return label ? `${kind} "${clean(label)}"` : `${kind} (unlabeled)`;
  };
  // the smallest labeled shape containing another gives wireframe structure ("button inside sidebar")
  const parent = (e: El) =>
    shapes
      .filter((s) => s.id !== e.id && boundText.has(s.id) && within(e, s))
      .sort((a, b) => a.width * a.height - b.width * b.height)[0];

  const lines: string[] = [];
  for (const e of alive) {
    if (SHAPES[e.type] || (e.type === "text" && !e.containerId)) {
      const p = parent(e);
      lines.push(name(e) + (p ? ` inside ${name(p)}` : ""));
    } else if (e.type === "arrow") {
      const from = name(byId.get(e.startBinding?.elementId ?? ""));
      const to = name(byId.get(e.endBinding?.elementId ?? ""));
      const label = boundText.get(e.id);
      const ends = from || to ? `${from || "(nothing)"} → ${to || "(nothing)"}` : "(not connected)";
      lines.push(`arrow ${ends}${label ? ` labeled "${clean(label)}"` : ""}`);
    } else if (e.type === "line") lines.push("line");
    else if (e.type === "freedraw") lines.push("freehand stroke");
    else if (e.type === "image") lines.push("image");
  }
  return lines.sort();
}
