import { useId, useState, type FormEvent } from "react";

import "../styles/Form.css";
import { signIn } from "../utils/accountsApi";
import { toApiError } from "../utils/http";
import { AlertIcon, LogoMark } from "./Icons";

interface Props {
  onSignedIn: () => void;
}

export default function LoginPage({ onSignedIn }: Props) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputId = useId();
  const errorId = useId();

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(password);
      setPassword("");
      onSignedIn();
    } catch (reason) {
      const apiError = toApiError(reason);
      setError(
        apiError.retryAfterSeconds
          ? `${apiError.message} (tente de novo em ${apiError.retryAfterSeconds} s)`
          : apiError.message,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="login" aria-labelledby="login-title">
      <LogoMark size={40} />
      <h1 id="login-title">Entrar no painel</h1>
      <p className="login__lead">
        O painel guarda tokens de acesso do Instagram e dispara coletas e análises, por isso pede a senha
        definida em <code>ADMIN_PASSWORD</code>.
      </p>
      <form className="analyze-form" onSubmit={handleSubmit} noValidate>
        <label className="analyze-form__label" htmlFor={inputId}>
          Senha
        </label>
        <div className="analyze-form__row">
          <input
            id={inputId}
            className="analyze-form__input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => {
              setPassword(event.target.value);
              if (error) setError(null);
            }}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? errorId : undefined}
            disabled={busy}
          />
          <button type="submit" className="button button--primary button--large" disabled={busy}>
            {busy ? "Entrando…" : "Entrar"}
          </button>
        </div>
        {error && (
          <p id={errorId} className="analyze-form__error" role="alert">
            <AlertIcon size={18} />
            <span>{error}</span>
          </p>
        )}
      </form>
    </section>
  );
}
