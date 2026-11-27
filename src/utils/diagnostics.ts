import { call as deckyCall } from "@decky/api";

const session = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
let sequence = 0;
let pending: string[] = [];
let sending = false;
let timer: ReturnType<typeof setInterval> | undefined;
const storageKey = "decky-yomi-diagnostics";

export function trace(event: string, detail = "") {
  const line = JSON.stringify({ time: new Date().toISOString(), session, event, detail: detail.slice(0, 3000) });
  console.info("[Decky Yomi]", line);
  pending.push(line);
  pending = pending.slice(-100);
  try { localStorage.setItem(storageKey, JSON.stringify(pending)); } catch {}
}
async function flush() {
  if (sending || !pending.length) return;
  sending = true;
  try {
    while (pending.length) {
      const batch = pending.slice(0, 25);
      await deckyCall<[string[]], void>("log_frontend", batch);
      pending = pending.filter(line => !batch.includes(line));
      try { localStorage.setItem(storageKey, JSON.stringify(pending)); } catch {}
    }
  } catch { /* Keep bounded breadcrumbs locally until the backend reconnects. */ }
  finally { sending = false; }
}
export const call = async <Args extends any[] = [], Return = void>(route: string, ...args: Args): Promise<Return> => {
  const id = ++sequence;
  const start = performance.now();
  trace("rpc.start", `${id} ${route}`);
  try {
    const result = await deckyCall<Args, Return>(route, ...args);
    const error = (result as any)?.error;
    trace(error ? "rpc.error_response" : "rpc.end", `${id} ${route} ${Math.round(performance.now() - start)}ms${error ? ` ${error}` : ""}`);
    return result;
  } catch (error) {
    trace("rpc.throw", `${id} ${route} ${String(error)}`);
    throw error;
  }
};
export function isOwnDiagnosticSource(source: string): boolean {
  if (/decky[-_ ]yomi/i.test(source)) return true;
  if (/\/plugins\/|https?:\/\/|sp:\/\//i.test(source)) return false;
  return true;
}

export function startDiagnostics() {
  try { const old = JSON.parse(localStorage.getItem(storageKey) || "[]"); if (Array.isArray(old)) pending = old.filter(x => typeof x === "string").slice(-100); } catch {}
  const onError = (e: ErrorEvent) => {
    const stack = e.error?.stack || "";
    const filename = e.filename || "";
    if (!filename && !stack && e.message === "Script error.") return;
    if (!isOwnDiagnosticSource(`${filename} ${stack}`)) return;
    trace("window.error", `${e.message} ${filename}:${e.lineno} ${stack}`);
  };
  const onRejection = (e: PromiseRejectionEvent) => {
    const detail = String(e.reason?.stack || e.reason || "");
    if (!isOwnDiagnosticSource(detail)) return;
    trace("window.unhandledrejection", detail);
  };
  const onPageHide = () => trace("window.pagehide");
  window.addEventListener("error", onError);
  window.addEventListener("unhandledrejection", onRejection);
  window.addEventListener("pagehide", onPageHide);
  trace("frontend.start", "version=0.1.9");
  timer = setInterval(() => { void flush(); }, 2000);
  void flush();
  return () => {
    trace("frontend.dismount");
    clearInterval(timer);
    window.removeEventListener("error", onError);
    window.removeEventListener("unhandledrejection", onRejection);
    window.removeEventListener("pagehide", onPageHide);
    void flush();
  };
}
