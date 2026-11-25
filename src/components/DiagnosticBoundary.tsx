import { Component, ReactNode, ErrorInfo } from "react";
import { trace } from "../utils/diagnostics";
export class DiagnosticBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error, info: ErrorInfo) {
    trace("react.render_error", `${error.stack || error.message}\n${info.componentStack}`);
  }
  render() {
    return this.state.failed
      ? <div role="alert" style={{ padding: 16 }}>Decky Yomi encountered an error. Diagnostics have been recorded. Close and reopen the plugin.</div>
      : this.props.children;
  }
}
