import { useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import { Excalidraw, exportToBlob, serializeAsJSON } from "@excalidraw/excalidraw";
import type { ExcalidrawImperativeAPI, ExcalidrawInitialDataState } from "@excalidraw/excalidraw/types";
import "@excalidraw/excalidraw/index.css";
import { api } from "../api";
import { describe } from "../lib/describe";
import { prefersDark } from "../lib/util";

export interface WhiteboardHandle {
  /** Push the current drawing to the server now (used before sending a message). */
  sync(): Promise<void>;
}

interface Props {
  sessionId: string;
  readOnly: boolean;
  onSynced?: (at: Date) => void;
  ref?: Ref<WhiteboardHandle>;
}

const blobToDataUrl = (blob: Blob) =>
  new Promise<string>((res) => {
    const r = new FileReader();
    r.onload = () => res(r.result as string);
    r.readAsDataURL(blob);
  });

export default function Whiteboard({ sessionId, readOnly, onSynced, ref }: Props) {
  const excalidraw = useRef<ExcalidrawImperativeAPI | null>(null);
  const lastKey = useRef("");
  const lastSynced = useRef("");
  const timer = useRef<number>(0);
  // old sessions may only have a PNG snapshot of the board, no editable scene
  const [snapshotOnly, setSnapshotOnly] = useState(false);

  // Excalidraw accepts a promise: the saved scene loads without a flash of an empty board.
  const initialData = useMemo(
    () => api.scene(sessionId).then((scene) => {
      const s = scene as { elements?: unknown[]; files?: unknown } | null;
      if (!s && readOnly) setSnapshotOnly(true);
      return {
        elements: s?.elements ?? [],
        files: s?.files ?? {},
        appState: { viewBackgroundColor: "#ffffff" },
        scrollToContent: true,
      } as unknown as ExcalidrawInitialDataState;
    }),
    [sessionId, readOnly],
  );

  async function sync() {
    const ex = excalidraw.current;
    if (!ex || readOnly) return;
    const elements = ex.getSceneElements();
    const key = elements.map((e) => `${e.id}:${e.version}`).join(",");
    if (key === lastSynced.current) return;   // nothing new since the last push
    lastSynced.current = key;
    const files = ex.getFiles();
    const png = elements.length
      ? await blobToDataUrl(await exportToBlob({
          elements, files, mimeType: "image/png", maxWidthOrHeight: 1600, exportPadding: 24,
          appState: { exportBackground: true, viewBackgroundColor: "#ffffff", exportWithDarkMode: false },
        }))
      : null;
    const scene = serializeAsJSON(elements, ex.getAppState(), files, "local");
    await api.canvas(sessionId, { lines: describe(elements), png, scene });
    onSynced?.(new Date());
  }

  useImperativeHandle(ref, () => ({ sync }));

  if (snapshotOnly) {
    return (
      <div className="board-snapshot">
        <img src={`/files/${encodeURIComponent(sessionId)}/canvas.png`} alt="Último boceto de la sesión"
          onError={(e) => { e.currentTarget.replaceWith(Object.assign(document.createElement("p"), { textContent: "Esta sesión no tiene pizarra guardada.", className: "muted" })); }} />
      </div>
    );
  }

  return (
    <Excalidraw
      excalidrawAPI={(a) => (excalidraw.current = a)}
      initialData={initialData}
      viewModeEnabled={readOnly}
      theme={prefersDark() ? "dark" : "light"}
      UIOptions={{ canvasActions: { loadScene: false, saveToActiveFile: false, export: false } }}
      onChange={(elements) => {
        if (readOnly) return;
        // onChange also fires on selection/scroll: only react to real edits
        const key = elements.map((e) => `${e.id}:${e.version}`).join(",");
        if (key === lastKey.current) return;
        lastKey.current = key;
        clearTimeout(timer.current);
        timer.current = window.setTimeout(() => void sync(), 1500);
      }}
    />
  );
}
