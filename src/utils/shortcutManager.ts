import { call } from "@decky/api";
import type { AnalysisResult, Settings } from "./types";

export interface RegionPreview {
  visible: boolean;
  x: number;
  y: number;
  w: number;
  h: number;
  pinned?: boolean;
}

export interface OverlayState {
  visible: boolean;
  loading: boolean;
  loadingMessage: string;
  result: AnalysisResult | null;
  error: string | null;
  holding: boolean;
  triggerButton: string;
  regionPreview: RegionPreview;
}

export function resolveRegionBox(
  settings: Partial<
    Pick<
      Settings,
      "ocr_mode" | "custom_region_x" | "custom_region_y" | "custom_region_w" | "custom_region_h"
    >
  >
): { x: number; y: number; w: number; h: number } {
  const mode = settings.ocr_mode || "bottom_30";
  switch (mode) {
    case "fullscreen":
      return { x: 0, y: 0, w: 100, h: 100 };
    case "bottom_40":
      return { x: 0, y: 60, w: 100, h: 40 };
    case "center":
      return { x: 15, y: 25, w: 70, h: 50 };
    case "custom":
      return {
        x: settings.custom_region_x ?? 0,
        y: settings.custom_region_y ?? 70,
        w: settings.custom_region_w ?? 100,
        h: settings.custom_region_h ?? 30,
      };
    case "bottom_30":
    case "region":
    default:
      return { x: 0, y: 70, w: 100, h: 30 };
  }
}

export type OverlayAction =
  | "prev_word"
  | "next_word"
  | "prev_defined"
  | "next_defined"
  | "prev_entry"
  | "next_entry"
  | "save_word"
  | "recapture"
  | "dismiss";

type StateListener = (state: OverlayState) => void;
type ActionListener = (action: OverlayAction) => void;

class ShortcutManager {
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private holdStartTime: number | null = null;
  private isTriggered = false;
  private listeners: Set<StateListener> = new Set();
  private actionListeners: Set<ActionListener> = new Set();
  private autoDismissTimer: ReturnType<typeof setTimeout> | null = null;
  private previewTimer: ReturnType<typeof setTimeout> | null = null;
  private prevButtons: Set<string> = new Set();
  private navRepeatState: { button: string; nextAt: number } | null = null;

  private state: OverlayState = {
    visible: false,
    loading: false,
    loadingMessage: "",
    result: null,
    error: null,
    holding: false,
    triggerButton: "L4",
    regionPreview: { visible: false, x: 0, y: 70, w: 100, h: 30, pinned: false },
  };

  private currentTrigger: string = "L4";
  private isRunning = false;

  constructor() {
    this.refreshSettings();
  }

  public async refreshSettings(): Promise<void> {
    try {
      const settings = await call<[], Settings>("get_settings");
      if (settings && settings.hotkey_trigger) {
        this.currentTrigger = settings.hotkey_trigger;
      }
    } catch {
      this.currentTrigger = "L4";
    }
  }

  public getState(): OverlayState {
    return { ...this.state };
  }

  public subscribe(listener: StateListener): () => void {
    this.listeners.add(listener);
    listener(this.getState());
    return () => {
      this.listeners.delete(listener);
    };
  }

  public onAction(listener: ActionListener): () => void {
    this.actionListeners.add(listener);
    return () => {
      this.actionListeners.delete(listener);
    };
  }

  private emitAction(action: OverlayAction): void {
    this.resetAutoDismiss();
    this.actionListeners.forEach((fn) => fn(action));
  }

  public resetAutoDismiss(ms = 30000): void {
    if (this.autoDismissTimer) {
      clearTimeout(this.autoDismissTimer);
      this.autoDismissTimer = null;
    }
    if (this.state.visible && !this.state.loading) {
      this.autoDismissTimer = setTimeout(() => {
        this.dismiss();
      }, ms);
    }
  }

  private updateState(partial: Partial<OverlayState>): void {
    this.state = { ...this.state, ...partial };
    const current = this.getState();
    this.listeners.forEach((fn) => fn(current));
  }

  public start(): void {
    if (this.isRunning) return;
    this.isRunning = true;
    this.refreshSettings();

    this.pollTimer = setInterval(async () => {
      await this.pollButtons();
    }, 80);
  }

  public stop(): void {
    this.isRunning = false;
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (this.autoDismissTimer) {
      clearTimeout(this.autoDismissTimer);
      this.autoDismissTimer = null;
    }
    if (this.previewTimer) {
      clearTimeout(this.previewTimer);
      this.previewTimer = null;
    }
  }

  public showRegionPreview(
    box: { x: number; y: number; w: number; h: number },
    pinned = false
  ): void {
    if (this.previewTimer) {
      clearTimeout(this.previewTimer);
      this.previewTimer = null;
    }
    const clampedX = Math.max(0, Math.min(95, box.x));
    const clampedY = Math.max(0, Math.min(95, box.y));
    const clampedW = Math.max(5, Math.min(100 - clampedX, box.w));
    const clampedH = Math.max(5, Math.min(100 - clampedY, box.h));

    this.updateState({
      regionPreview: {
        visible: true,
        x: clampedX,
        y: clampedY,
        w: clampedW,
        h: clampedH,
        pinned,
      },
    });

    if (!pinned) {
      this.previewTimer = setTimeout(() => {
        this.hideRegionPreview();
      }, 4500);
    }
  }

  public hideRegionPreview(): void {
    if (this.previewTimer) {
      clearTimeout(this.previewTimer);
      this.previewTimer = null;
    }
    if (this.state.regionPreview.visible) {
      this.updateState({
        regionPreview: { ...this.state.regionPreview, visible: false, pinned: false },
      });
    }
  }

  public dismiss(): void {
    if (this.autoDismissTimer) {
      clearTimeout(this.autoDismissTimer);
      this.autoDismissTimer = null;
    }
    this.updateState({
      visible: false,
      loading: false,
      error: null,
    });
  }

  public async triggerCapture(region?: any): Promise<void> {
    if (this.state.loading) return;

    if (this.autoDismissTimer) {
      clearTimeout(this.autoDismissTimer);
      this.autoDismissTimer = null;
    }

    // Ensure holding badge & region guide are hidden before taking screenshot
    this.hideRegionPreview();
    this.updateState({
      visible: false,
      holding: false,
      loading: true,
      loadingMessage: "Capturing screen...",
      error: null,
      result: null,
    });

    // Give Gamescope/CEF a frame (~90ms) to paint without any overlay before PipeWire captures
    await new Promise((r) => setTimeout(r, 90));

    // Show the loading indicator only after capture_screen (~230ms) has grabbed the clean frame
    const loadingRevealTimer = setTimeout(() => {
      if (this.state.loading) {
        this.updateState({
          visible: true,
          loadingMessage: "Reading Japanese dialogue...",
        });
      }
    }, 450);

    try {
      const result = await call<[region: any], AnalysisResult>(
        "capture_and_analyze",
        region ?? null
      );
      clearTimeout(loadingRevealTimer);

      if (result.error) {
        this.updateState({
          visible: true,
          loading: false,
          error: result.error,
          result: null,
        });
      } else {
        this.updateState({
          visible: true,
          loading: false,
          result,
          error: null,
        });

        // Auto-dismiss after 30s of inactivity
        this.resetAutoDismiss(30000);
      }
    } catch (e) {
      clearTimeout(loadingRevealTimer);
      this.updateState({
        visible: true,
        loading: false,
        error: String(e),
        result: null,
      });
    }
  }

  private handleOverlayNavigation(buttons: Set<string>): void {
    const now = Date.now();
    const newlyPressed = new Set<string>();
    buttons.forEach((b) => {
      if (!this.prevButtons.has(b)) newlyPressed.add(b);
    });

    if (newlyPressed.has("B")) {
      this.dismiss();
      return;
    }
    if (this.state.error || !this.state.result) {
      if (newlyPressed.has("Y")) {
        this.emitAction("recapture");
      }
      return;
    }

    // Single-press actions
    if (newlyPressed.has("A") || newlyPressed.has("X")) {
      this.emitAction("save_word");
    } else if (newlyPressed.has("Y")) {
      this.emitAction("recapture");
    } else if (newlyPressed.has("L1")) {
      this.emitAction("prev_defined");
    } else if (newlyPressed.has("R1")) {
      this.emitAction("next_defined");
    } else if (newlyPressed.has("DPAD_UP")) {
      this.emitAction("prev_entry");
    } else if (newlyPressed.has("DPAD_DOWN")) {
      this.emitAction("next_entry");
    }

    // D-pad Left/Right with hold-to-repeat
    const dirBtn = buttons.has("DPAD_LEFT")
      ? "DPAD_LEFT"
      : buttons.has("DPAD_RIGHT")
      ? "DPAD_RIGHT"
      : null;

    if (dirBtn) {
      if (newlyPressed.has(dirBtn)) {
        this.emitAction(dirBtn === "DPAD_LEFT" ? "prev_word" : "next_word");
        this.navRepeatState = { button: dirBtn, nextAt: now + 340 };
      } else if (this.navRepeatState && this.navRepeatState.button === dirBtn && now >= this.navRepeatState.nextAt) {
        this.emitAction(dirBtn === "DPAD_LEFT" ? "prev_word" : "next_word");
        this.navRepeatState = { button: dirBtn, nextAt: now + 120 };
      }
    } else {
      this.navRepeatState = null;
    }
  }

  private async pollButtons(): Promise<void> {
    if (this.currentTrigger === "none" && !this.state.visible) return;

    try {
      const res = await call<[], { success: boolean; buttons: string[] }>(
        "get_hidraw_button_state"
      );
      if (!res || !res.success) return;

      const buttons = new Set(res.buttons || []);

      if (this.state.visible && !this.state.loading) {
        this.handleOverlayNavigation(buttons);
      } else {
        this.navRepeatState = null;
      }

      const isTargetPressed = this.currentTrigger !== "none" && this.isTriggerActive(buttons);

      if (isTargetPressed) {
        const now = Date.now();
        if (this.holdStartTime === null) {
          this.holdStartTime = now;
          this.isTriggered = false;
          if (!this.state.visible) {
            this.updateState({ holding: true, triggerButton: this.currentTrigger });
          }
        } else if (!this.isTriggered && now - this.holdStartTime >= 380) {
          // Held for >= 380ms
          this.isTriggered = true;
          this.updateState({ holding: false });
          this.handleShortcutFired();
        }
      } else {
        if (this.holdStartTime !== null) {
          this.updateState({ holding: false });
        }
        this.holdStartTime = null;
        this.isTriggered = false;
      }

      this.prevButtons = buttons;
    } catch {
      // Ignore polling errors
    }
  }

  private isTriggerActive(buttons: Set<string>): boolean {
    switch (this.currentTrigger) {
      case "L4":
        return buttons.has("L4");
      case "R4":
        return buttons.has("R4");
      case "L5":
        return buttons.has("L5");
      case "R5":
        return buttons.has("R5");
      case "select":
        return buttons.has("SELECT");
      default:
        return buttons.has("L4");
    }
  }

  private handleShortcutFired(): void {
    if (this.state.visible) {
      // If overlay is already up, pressing the shortcut dismisses it
      this.dismiss();
    } else {
      // Trigger new capture
      this.triggerCapture();
    }
  }
}

export const shortcutManager = new ShortcutManager();
