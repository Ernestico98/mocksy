import { describe, expect, it } from "vitest";
import type { PackData } from "../api";
import { duplicate, emptyForm, fromForm, phaseKey, slugify, toForm, uniqueId, validate, type PackForm } from "./packs";

const pack: PackData = {
  id: "system-design", title: "System Design", description: "d", company: "", default_language: "en",
  whiteboard: true, builtin: true, rubric: ["Scoping", "Trade-offs"], body: "Brief.", extra: { custom: 1 },
  phases: [{ key: "requirements", label: "Requirements", minutes: 10 }, { key: "deep_dive", label: "Deep dive", minutes: 20 }],
};

const valid = (): PackForm => ({ ...toForm(pack), id: "mine" });

describe("validate", () => {
  it("accepts a valid pack", () => {
    expect(validate(valid(), ["system-design"])).toEqual({});
  });

  it("requires a title, a brief, phases and a rubric", () => {
    const e = validate({ ...emptyForm(), id: "x", phases: [] }, []);
    expect(Object.keys(e).sort()).toEqual(["body", "phases", "rubric", "title"]);
  });

  it("checks the file name", () => {
    for (const id of ["", "../evil", "Caps", "-lead", "a/b", "x".repeat(65)]) {
      expect(validate({ ...valid(), id }, []).id).toBeTruthy();
    }
    expect(validate(valid(), ["mine"]).id).toMatch(/already exists/);
  });

  it("flags duplicate phase keys, bad keys, empty labels and bad durations per row", () => {
    const f = valid();
    f.phases = [
      { key: "a", label: "A", minutes: "5" },
      { key: "a", label: "", minutes: "0" },
      { key: "Bad key", label: "C", minutes: "2.5" },
      { key: "d", label: "D", minutes: "181" },
      { key: "e", label: "E", minutes: "-3" },
    ];
    const e = validate(f, []);
    expect(e["phases.0.key"]).toBeUndefined();
    expect(e["phases.1.key"]).toMatch(/Duplicate/);
    expect(e["phases.1.label"]).toBeTruthy();
    expect(e["phases.1.minutes"]).toBeTruthy();
    expect(e["phases.2.key"]).toBeTruthy();
    expect(e["phases.2.minutes"]).toBeTruthy();
    expect(e["phases.3.minutes"]).toBeTruthy();
    expect(e["phases.4.minutes"]).toBeTruthy();
  });

  it("wants unique rubric dimensions and ignores blank lines", () => {
    expect(validate({ ...valid(), rubric: "A\n\n A \n" }, []).rubric).toMatch(/unique/);
    expect(validate({ ...valid(), rubric: "A\n\nB" }, []).rubric).toBeUndefined();
  });
});

describe("form conversion", () => {
  it("round-trips through the form", () => {
    const { builtin: _, ...rest } = pack;
    expect(fromForm(toForm(pack))).toEqual(rest);
  });

  it("trims and keeps unknown header fields", () => {
    const f = { ...valid(), title: "  T  ", rubric: " A \n\nB\n" };
    const out = fromForm(f);
    expect(out.title).toBe("T");
    expect(out.rubric).toEqual(["A", "B"]);
    expect(out.extra).toEqual({ custom: 1 });
  });
});

describe("ids", () => {
  it("slugifies titles into file names", () => {
    expect(slugify("Product Sense: Round 2!")).toBe("product-sense-round-2");
    expect(slugify("Entrevista técnica")).toBe("entrevista-tecnica");
    expect(slugify("  --  ")).toBe("");
    expect(slugify("a".repeat(80)).length).toBe(64);
  });

  it("derives phase keys from labels", () => {
    expect(phaseKey("High-level design")).toBe("high_level_design");
    expect(phaseKey("2nd round")).toBe("nd_round");
  });

  it("picks a free id", () => {
    expect(uniqueId("x", [])).toBe("x");
    expect(uniqueId("x", ["x", "x-2"])).toBe("x-3");
    expect(uniqueId("", [])).toBe("interview");
  });

  it("duplicates a built-in into a personal copy", () => {
    const d = duplicate(pack, ["system-design", "system-design-copy"]);
    expect(d.id).toBe("system-design-copy-2");
    expect(d.title).toBe("System Design (copy)");
    expect(validate(d, ["system-design", "system-design-copy"])).toEqual({});
  });
});
