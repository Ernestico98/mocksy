import { useCallback, useEffect, useState } from "react";
import { api, subscribe, type AppMeta } from "./api";
import { ConfirmProvider } from "./components/Confirm";
import Home from "./pages/Home";
import Packs, { type PackRoute } from "./pages/Packs";
import SessionView from "./pages/SessionView";

type Route = { page: "home" } | { page: "session"; id: string } | { page: "packs"; route: PackRoute };

// Tiny hash router: "#/" home, "#/s/<id>" a session, "#/packs[/new[/<from>]|/edit/<id>]" interview types.
function read(): Route {
  const h = location.hash;
  let m = h.match(/^#\/s\/(.+)$/);
  if (m) return { page: "session", id: decodeURIComponent(m[1]) };
  if (!h.startsWith("#/packs")) return { page: "home" };
  if ((m = h.match(/^#\/packs\/edit\/(.+)$/))) return { page: "packs", route: { mode: "edit", id: decodeURIComponent(m[1]) } };
  if ((m = h.match(/^#\/packs\/new(?:\/(.+))?$/))) return { page: "packs", route: { mode: "new", from: m[1] && decodeURIComponent(m[1]) } };
  return { page: "packs", route: { mode: "list" } };
}

function useRoute() {
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export default function App() {
  const route = useRoute();
  const [meta, setMeta] = useState<AppMeta | null>(null);
  const [error, setError] = useState("");

  const reload = useCallback(() => api.meta().then(setMeta).catch((e: Error) => setError(e.message)), []);

  useEffect(() => {
    void reload();
    return subscribe((ev) => {
      if (ev.type === "settings") setMeta((m) => (m ? { ...m, settings: ev.settings } : m));
    });
  }, [reload]);

  if (error) return <div className="boot">Can't reach the server ({error}). Is <code>./run.sh</code> running?</div>;
  if (!meta) return <div className="boot">Loading…</div>;

  return (
    <ConfirmProvider>
      {route.page === "session" ? <SessionView key={route.id} id={route.id} meta={meta} />
        : route.page === "packs" ? <Packs route={route.route} meta={meta} onChanged={reload} />
        : <Home meta={meta} />}
    </ConfirmProvider>
  );
}
