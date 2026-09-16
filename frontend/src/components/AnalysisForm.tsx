import { useId, useState, type FormEvent } from "react";

import "../styles/Form.css";
import { URL_HINT, validateInstagramUrl } from "../utils/validation";
import { AlertIcon } from "./Icons";

interface Props {
  onSubmit: (url: string) => void;
  busy?: boolean;
  error?: string | null;
  initialUrl?: string;
}

export default function AnalysisForm({ onSubmit, busy = false, error = null, initialUrl = "" }: Props) {
  const [url, setUrl] = useState(initialUrl);
  const [validationError, setValidationError] = useState<string | null>(null);
  const inputId = useId();
  const hintId = useId();
  const errorId = useId();

  const message = validationError ?? error;

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const result = validateInstagramUrl(url);
    if (!result.ok) {
      setValidationError(result.message);
      return;
    }
    setValidationError(null);
    setUrl(result.canonicalUrl);
    onSubmit(result.canonicalUrl);
  };

  return (
    <form className="analyze-form" onSubmit={handleSubmit} noValidate aria-busy={busy}>
      <label htmlFor={inputId} className="analyze-form__label">
        Link do Reel ou post
      </label>
      <div className="analyze-form__row">
        <input
          id={inputId}
          name="url"
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://www.instagram.com/reel/…"
          className="analyze-form__input"
          value={url}
          onChange={(event) => {
            setUrl(event.target.value);
            if (validationError) setValidationError(null);
          }}
          aria-invalid={message ? true : undefined}
          aria-describedby={message ? `${errorId} ${hintId}` : hintId}
          disabled={busy}
        />
        <button type="submit" className="button button--primary button--large" disabled={busy}>
          {busy ? "Enviando…" : "Analisar comentários"}
        </button>
      </div>
      <p id={hintId} className="analyze-form__hint">
        {URL_HINT}
      </p>
      {message && (
        <p id={errorId} className="analyze-form__error" role="alert">
          <AlertIcon size={18} />
          <span>{message}</span>
        </p>
      )}
    </form>
  );
}
