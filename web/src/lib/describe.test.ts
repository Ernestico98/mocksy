import { describe as suite, expect, it } from "vitest";
import { describe } from "./describe";

let n = 0;
const box = (label: string | null, x = 0, y = 0, w = 100, h = 50, type = "rectangle") => {
  const id = `s${++n}`;
  const els: Record<string, unknown>[] = [{ id, type, x, y, width: w, height: h }];
  if (label) els.push({ id: `t${n}`, type: "text", text: label, containerId: id, x, y, width: 10, height: 10 });
  return { id, els };
};
const arrow = (from?: string, to?: string, label?: string) => {
  const id = `a${++n}`;
  const els: Record<string, unknown>[] = [{
    id, type: "arrow", x: 0, y: 0, width: 1, height: 1,
    startBinding: from ? { elementId: from } : null, endBinding: to ? { elementId: to } : null,
  }];
  if (label) els.push({ id: `t${n}`, type: "text", text: label, containerId: id, x: 0, y: 0, width: 1, height: 1 });
  return els;
};

suite("describe(whiteboard)", () => {
  it("labels shapes with their bound text", () => {
    const api = box("API");
    const db = box("Postgres", 300, 0, 80, 80, "ellipse");
    expect(describe([...api.els, ...db.els])).toEqual(['box "API"', 'circle "Postgres"']);
  });

  it("describes arrows by what they connect", () => {
    const a = box("API"), q = box("Queue", 300);
    const lines = describe([...a.els, ...q.els, ...arrow(a.id, q.id, "enqueue"), ...arrow(), ...arrow(a.id)]);
    expect(lines).toContain('arrow box "API" → box "Queue" labeled "enqueue"');
    expect(lines).toContain("arrow (not connected)");
    expect(lines).toContain('arrow box "API" → (nothing)');
  });

  it("nests shapes inside the smallest labeled container (wireframe structure)", () => {
    const screen = box("Reviewer screen", 0, 0, 1000, 800);
    const panel = box("Comments", 700, 50, 250, 600);
    const button = box("Approve", 720, 560, 100, 40);
    const lines = describe([...screen.els, ...panel.els, ...button.els]);
    expect(lines).toContain('box "Approve" inside box "Comments"');
    expect(lines).toContain('box "Comments" inside box "Reviewer screen"');
  });

  it("ignores deleted elements, normalizes text, keeps unlabeled shapes", () => {
    const gone = box("Old");
    (gone.els[0] as { isDeleted?: boolean }).isDeleted = true;
    const lines = describe([
      ...gone.els,
      ...box(null).els,
      { id: "free", type: "text", text: "  Episode 1\n   %progress ", x: 5000, y: 0, width: 1, height: 1 },
      { id: "fd", type: "freedraw", x: 0, y: 0, width: 1, height: 1 },
    ]);
    expect(lines).toEqual(["box (unlabeled)", "freehand stroke", 'text "Episode 1 %progress"']);
  });

  it("is order independent so diffs are stable", () => {
    const a = box("A"), b = box("B", 300);
    expect(describe([...a.els, ...b.els])).toEqual(describe([...b.els, ...a.els]));
  });
});
