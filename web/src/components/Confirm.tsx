import { createContext, useCallback, useContext, useState, type ReactNode } from "react";

type Ask = (text: string, yes?: string) => Promise<boolean>;

const Ctx = createContext<Ask>(async () => false);

/** In-app confirmation toast (replaces window.confirm). */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<{ text: string; yes: string; resolve: (v: boolean) => void } | null>(null);

  const ask = useCallback<Ask>(
    (text, yes = "Yes") => new Promise((resolve) => setPending({ text, yes, resolve })),
    [],
  );

  const done = (v: boolean) => {
    pending?.resolve(v);
    setPending(null);
  };

  return (
    <Ctx.Provider value={ask}>
      {children}
      {pending && (
        <div className="toast" role="alertdialog" aria-live="assertive">
          <span>{pending.text}</span>
          <button className="btn primary" autoFocus onClick={() => done(true)}>{pending.yes}</button>
          <button className="btn" onClick={() => done(false)}>Cancel</button>
        </div>
      )}
    </Ctx.Provider>
  );
}

export const useConfirm = () => useContext(Ctx);
