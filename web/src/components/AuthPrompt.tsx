import { useEffect, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { getToken, onUnauthorized, setToken } from "../api/auth";

/** Modal shown when the server answers 401 (api_token configured): asks for
 * the API token, stores it in localStorage and reloads so every query,
 * media URL and the WebSocket pick it up. Invisible in open LAN mode. */
export default function AuthPrompt() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");

  useEffect(() => onUnauthorized(() => setOpen(true)), []);

  if (!open) return null;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!draft.trim()) return;
    setToken(draft.trim());
    window.location.reload();
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={t("auth.title")}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(20, 18, 12, 0.55)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <form className="card" style={{ width: 380, maxWidth: "90vw" }} onSubmit={submit}>
        <div className="card-title">{t("auth.title")}</div>
        <p style={{ marginTop: 0, color: "var(--ink-2)", fontSize: 13 }}>{t("auth.hint")}</p>
        <div className="field">
          <label htmlFor="api-token">{t("auth.tokenLabel")}</label>
          <input
            id="api-token"
            type="password"
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={t("auth.placeholder")}
          />
        </div>
        {getToken() && (
          <p style={{ color: "var(--critical)", fontSize: 12 }}>{t("auth.invalidStored")}</p>
        )}
        <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
          <button className="btn primary" type="submit" disabled={!draft.trim()}>
            {t("auth.save")}
          </button>
        </div>
      </form>
    </div>
  );
}
