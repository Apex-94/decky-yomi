import { definePlugin, routerHook } from "@decky/api";
import { staticClasses } from "@decky/ui";
import { FaLanguage } from "react-icons/fa";
import { MainPanel } from "./components/MainPanel";
import { InGameOverlay } from "./components/InGameOverlay";
import { shortcutManager } from "./utils/shortcutManager";
import { startDiagnostics } from "./utils/diagnostics";
import { DiagnosticBoundary } from "./components/DiagnosticBoundary";

export default definePlugin(() => {
  const stopDiagnostics = startDiagnostics();
  shortcutManager.start();

  // Register in-game overlay globally across Steam CEF sessions
  if (routerHook && typeof routerHook.addGlobalComponent === "function") {
    routerHook.addGlobalComponent("DeckyYomiOverlay", InGameOverlay);
  }

  return {
    name: "Decky Yomi",
    titleView: <div className={staticClasses.Title}>Decky Yomi</div>,
    content: (
      <DiagnosticBoundary>
        <MainPanel />
      </DiagnosticBoundary>
    ),
    icon: <FaLanguage />,
    alwaysRender: true,
    onDismount() {
      shortcutManager.stop();
      if (routerHook && typeof routerHook.removeGlobalComponent === "function") {
        routerHook.removeGlobalComponent("DeckyYomiOverlay");
      }
      stopDiagnostics();
    },
  };
});
