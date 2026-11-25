import { useEffect, useState, useRef, FC } from "react";
import {
  PanelSection,
  PanelSectionRow,
  ButtonItem,
  DialogButton,
  Focusable,
} from "@decky/ui";
import { call } from "../utils/diagnostics";
import { toaster } from "@decky/api";
import type { ComponentsStatus, InstallProgress } from "../utils/types";

interface DownloadManagerProps {
  onInstalled?: () => void;
}

export const DownloadManager: FC<DownloadManagerProps> = ({ onInstalled }) => {
  const [status, setStatus] = useState<ComponentsStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [showLogs, setShowLogs] = useState(false);
  const pollTimerRef = useRef<any>(null);

  const refreshStatus = async () => {
    try {
      const data = await call<[], ComponentsStatus>("get_components_status");
      setStatus(data);

      if (data.installing) {
        // Poll while downloading
        if (!pollTimerRef.current) {
          pollTimerRef.current = setInterval(async () => {
            try {
              const updated = await call<[], ComponentsStatus>("get_components_status");
              setStatus(updated);
              if (!updated.installing) {
                clearInterval(pollTimerRef.current);
                pollTimerRef.current = null;
                if (updated.install_progress.status === "completed") {
                  try {
                    toaster.toast({
                      title: "Decky Yomi",
                      body: "Engines downloaded & ready to use!",
                      duration: 4000,
                    });
                  } catch {}
                  onInstalled?.();
                }
              }
            } catch (e) {
              console.error("Poll error:", e);
            }
          }, 1500);
        }
      } else if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    } catch (e) {
      console.error("Failed to fetch component status:", e);
    }
    setLoading(false);
  };

  useEffect(() => {
    refreshStatus();
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    };
  }, []);

  const handleStartInstall = async (componentId: string = "all") => {
    try {
      const res = await call<[string], { started: boolean; error?: string }>(
        "start_install",
        componentId
      );
      if (res.started) {
        refreshStatus();
      } else {
        try {
          toaster.toast({
            title: "Decky Yomi",
            body: res.error || "Cannot start download",
            duration: 3500,
          });
        } catch {}
      }
    } catch (e) {
      console.error("Start install error:", e);
    }
  };

  const handleCancel = async () => {
    try {
      await call<[], any>("cancel_install");
      refreshStatus();
    } catch (e) {
      console.error("Cancel error:", e);
    }
  };

  if (!status) {
    return (
      <PanelSection title="📥 Setup & Downloads">
        <PanelSectionRow>
          <div style={{ textAlign: "center", opacity: 0.5 }}>Checking components...</div>
        </PanelSectionRow>
      </PanelSection>
    );
  }

  const isInstalling = status.installing;
  const progress: InstallProgress = status.install_progress;

  const box: React.CSSProperties = {
    boxSizing: "border-box", width: "100%", maxWidth: "100%", minWidth: 0,
    padding: 12, borderRadius: 8, border: "1px solid rgba(255,255,255,0.16)",
    background: "rgba(255,255,255,0.04)", overflowWrap: "anywhere",
  };
  const button: React.CSSProperties = { boxSizing: "border-box", width: "100%", minWidth: 0, maxWidth: "100%", margin: 0 };
  return (
    <div style={{ width: "100%", maxWidth: "100%", minWidth: 0, boxSizing: "border-box" }}>
      <PanelSection title="Setup & Downloads">
        <div style={box}>
          <div role="status" style={{ fontSize: 13, lineHeight: 1.5, marginBottom: 12 }}>
            {isInstalling ? "Installing components…" : status.all_installed ? "All components are ready." : "Download the components needed for Japanese text recognition."}
          </div>
          {!status.all_installed && !isInstalling && (
            <DialogButton style={button} onClick={() => handleStartInstall("all")}>Download & Set Up</DialogButton>
          )}
          {isInstalling && <>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", fontSize: 12 }}>
              <span style={{ flex: 1, minWidth: 0 }}>{progress.current_step || "Installing…"}</span>
              <span style={{ flexShrink: 0 }}>{progress.percent}%</span>
            </div>
            <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress.percent} aria-label="Installation" style={{ height: 8, margin: "12px 0", borderRadius: 4, background: "rgba(255,255,255,0.12)", overflow: "hidden" }}>
              <div style={{ width: `${Math.max(0, Math.min(100, progress.percent))}%`, height: "100%", background: "#4b9cd3" }} />
            </div>
            <DialogButton style={button} onClick={handleCancel}>Cancel Download</DialogButton>
          </>}
          {progress.status === "error" && <div role="alert" style={{ color: "#ffaaaa", marginTop: 12, fontSize: 13 }}>{progress.error}</div>}
          {!!progress.logs?.length && <>
            <div style={{ marginTop: 8 }}><DialogButton style={button} onClick={() => setShowLogs(!showLogs)}>{showLogs ? "Hide Logs" : "Show Logs"}</DialogButton></div>
            {showLogs && <pre tabIndex={0} aria-label="Installation logs" style={{ boxSizing: "border-box", width: "100%", maxWidth: "100%", minWidth: 0, margin: "12px 0 0", padding: 8, borderRadius: 4, background: "rgba(0,0,0,0.4)", maxHeight: 160, overflow: "auto", whiteSpace: "pre", wordBreak: "normal", overflowWrap: "normal", fontSize: 12, lineHeight: 1.5 }}>{progress.logs.join("\n")}</pre>}
          </>}
        </div>
      </PanelSection>
      <PanelSection title="Individual Components">
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
          {status.components.map(comp => <div key={comp.id} style={box}>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{comp.name}</div>
            <div style={{ marginTop: 6, fontSize: 12, lineHeight: 1.5 }}>{comp.desc}</div>
            <div style={{ margin: "8px 0", fontSize: 12, color: comp.installed ? "#a5dfad" : "#c4c9cf" }}>{comp.installed ? "Ready" : `Not installed · ${comp.size}`}</div>
            {!comp.installed && !isInstalling && <DialogButton style={button} onClick={() => handleStartInstall(comp.id)}>Download</DialogButton>}
          </div>)}
          <DialogButton style={button} onClick={refreshStatus} disabled={isInstalling}>Refresh Status</DialogButton>
        </div>
      </PanelSection>
    </div>
  );
};
