import { Component, type ErrorInfo, type ReactNode } from "react";

import { AlertIcon } from "./Icons";

interface Props {
  children: ReactNode;
  onReset?: () => void;
}

interface State {
  error: Error | null;
}

/** Catches rendering errors so one broken widget never blanks the whole page. */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("render_error", error, info.componentStack);
  }

  private reset = () => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="boundary" role="alert">
        <AlertIcon size={28} />
        <h2>Esta tela não pôde ser exibida</h2>
        <p>
          Um erro inesperado interrompeu a renderização. Seus dados continuam salvos; volte ao início para
          tentar de novo.
        </p>
        <p className="boundary__detail">{this.state.error.message}</p>
        <div className="boundary__actions">
          <button type="button" className="button button--primary" onClick={this.reset}>
            Voltar ao início
          </button>
          <button type="button" className="button button--ghost" onClick={() => window.location.reload()}>
            Recarregar a página
          </button>
        </div>
      </div>
    );
  }
}
