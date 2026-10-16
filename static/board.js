// Excalidraw whiteboard: mounting, a text description for the interviewer,
// and debounced sync (description + PNG + scene) to the server.

const SHAPES = { rectangle: "box", ellipse: "circle", diamond: "diamond", frame: "frame" };

function clean(t) {
  return (t || "").replace(/\s+/g, " ").trim().slice(0, 80);
}

function within(inner, outer) {
  return inner.x >= outer.x && inner.y >= outer.y &&
    inner.x + inner.width <= outer.x + outer.width && inner.y + inner.height <= outer.y + outer.height;
}

/** Turn Excalidraw elements into stable, human-readable lines. */
export function describe(elements) {
  const alive = elements.filter((e) => !e.isDeleted);
  const byId = new Map(alive.map((e) => [e.id, e]));
  const boundText = new Map(alive.filter((e) => e.type === "text" && e.containerId).map((e) => [e.containerId, e.text]));
  const shapes = alive.filter((e) => SHAPES[e.type]);
  const name = (e) => {
    if (!e) return null;
    if (e.type === "text") return `text "${clean(e.text)}"`;
    const label = boundText.get(e.id);
    return label ? `${SHAPES[e.type] || e.type} "${clean(label)}"` : `${SHAPES[e.type] || e.type} (unlabeled)`;
  };
  // the smallest shape that fully contains another gives wireframe structure ("button inside sidebar")
  const parent = (e) => shapes
    .filter((s) => s.id !== e.id && within(e, s))
    .sort((a, b) => a.width * a.height - b.width * b.height)[0];

  const lines = [];
  for (const e of alive) {
    if (SHAPES[e.type] || (e.type === "text" && !e.containerId)) {
      const p = parent(e);
      lines.push(name(e) + (p && (boundText.get(p.id)) ? ` inside ${name(p)}` : ""));
    } else if (e.type === "arrow") {
      const from = name(byId.get(e.startBinding?.elementId));
      const to = name(byId.get(e.endBinding?.elementId));
      const label = boundText.get(e.id);
      const ends = from || to ? `${from || "(nothing)"} → ${to || "(nothing)"}` : "(not connected)";
      lines.push(`arrow ${ends}${label ? ` labeled "${clean(label)}"` : ""}`);
    } else if (e.type === "line") lines.push("line");
    else if (e.type === "freedraw") lines.push("freehand stroke");
    else if (e.type === "image") lines.push("image");
  }
  return lines.sort();
}

export class Board {
  constructor(el, { onSync, readOnly = false, theme = "light" }) {
    this.el = el;
    this.onSync = onSync;
    this.readOnly = readOnly;
    this.theme = theme;
    this.api = null;
    this.lastKey = "";
    this.timer = null;
  }

  mount(scene) {
    const Ex = window.ExcalidrawLib;
    if (!Ex || !window.React) {
      this.el.innerHTML = `<div class="board-fallback">No se pudo cargar Excalidraw (¿sin conexión?). Puedes describir tu diseño hablando.</div>`;
      return;
    }
    this.Ex = Ex;
    const initialData = {
      elements: scene?.elements || [],
      files: scene?.files || {},
      appState: { viewBackgroundColor: "#ffffff", currentItemFontFamily: 1 },
      scrollToContent: true,
    };
    this.root = window.ReactDOM.createRoot(this.el);
    this.root.render(window.React.createElement(Ex.Excalidraw, {
      excalidrawAPI: (a) => (this.api = a),
      initialData,
      viewModeEnabled: this.readOnly,
      theme: this.theme,
      UIOptions: { canvasActions: { loadScene: false, saveToActiveFile: false, export: false, saveAsImage: true } },
      onChange: (elements) => { if (!this.readOnly) this.#schedule(elements); },
    }));
  }

  unmount() {
    clearTimeout(this.timer);
    this.root?.unmount();
    this.root = null;
    this.api = null;
  }

  setTheme(theme) {
    this.theme = theme;
    this.api?.updateScene({ appState: { theme } });
  }

  #schedule(elements) {
    // onChange also fires for selection and scrolling; only sync real edits
    const key = elements.filter((e) => !e.isDeleted).map((e) => e.id + ":" + e.version).join(",");
    if (key === this.lastKey) return;
    this.lastKey = key;
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.sync(), 1500);
  }

  async sync() {
    if (!this.api) return;
    const elements = this.api.getSceneElements();
    const files = this.api.getFiles();
    let png = null;
    if (elements.length) {
      const blob = await this.Ex.exportToBlob({
        elements, files, mimeType: "image/png", maxWidthOrHeight: 1600, exportPadding: 24,
        appState: { exportBackground: true, viewBackgroundColor: "#ffffff", exportWithDarkMode: false },
      });
      png = await new Promise((res) => { const r = new FileReader(); r.onload = () => res(r.result); r.readAsDataURL(blob); });
    }
    const scene = this.Ex.serializeAsJSON(elements, this.api.getAppState(), files, "local");
    await this.onSync({ lines: describe(elements), png, scene });
  }
}
