import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useRefinerSettings } from "../api/hooks";
import type {
  RefinerProvider,
  RefinerSettingsUpdate,
  RefinerTestResult,
} from "../api/types";
import { useToast } from "./Toast";
import { Badge, Card, Skeleton } from "./ui";

export const OLLAMA_BASE_URL = "http://localhost:11434/v1";
export const LMSTUDIO_BASE_URL = "http://localhost:1234/v1";

const MODEL_PLACEHOLDER: Record<RefinerProvider, string> = {
  off: "claude-haiku-4-5",
  anthropic: "claude-haiku-4-5",
  openai_compatible: "qwen2.5vl",
};

interface FormState {
  provider: RefinerProvider;
  model: string;
  base_url: string;
  /** Password-input value; empty = keep the stored key. */
  apiKey: string;
  /** Explicitly clear the stored key on save (sends api_key: ""). */
  clearKey: boolean;
  confidence: string;
  matchMenuItems: boolean;
  maxPerMinute: string;
}

type TestState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "done"; result: RefinerTestResult }
  | { status: "error"; message: string };

/** Dashboard settings for the AI Event Refiner: persisted via
 * PUT /api/settings/refiner and applied to the engine without a restart. */
export default function RefinerSettingsCard() {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { data: settings, isLoading } = useRefinerSettings();
  const [form, setForm] = useState<FormState | null>(null);
  const [test, setTest] = useState<TestState>({ status: "idle" });
  const [saving, setSaving] = useState(false);

  // Seed the form once from the loaded settings (later refetches must not
  // clobber in-progress edits).
  useEffect(() => {
    if (settings && form === null) {
      setForm({
        provider: settings.provider,
        model: settings.model,
        base_url: settings.base_url,
        apiKey: "",
        clearKey: false,
        confidence: String(settings.only_below_confidence),
        matchMenuItems: settings.match_menu_items,
        maxPerMinute: String(settings.max_per_minute),
      });
    }
  }, [settings, form]);

  if (isLoading || !settings || !form) {
    return (
      <Card title={t("system.refinerSettingsTitle")}>
        <Skeleton height={24} count={4} />
      </Card>
    );
  }

  const set = (patch: Partial<FormState>) => setForm({ ...form, ...patch });

  const body = (): RefinerSettingsUpdate => {
    const request: RefinerSettingsUpdate = {
      provider: form.provider,
      model: form.model.trim() || MODEL_PLACEHOLDER[form.provider],
      base_url: form.base_url.trim() || OLLAMA_BASE_URL,
      only_below_confidence: Math.min(1, Math.max(0, Number(form.confidence) || 0)),
      match_menu_items: form.matchMenuItems,
      max_per_minute: Math.max(0, Math.round(Number(form.maxPerMinute) || 0)),
    };
    // api_key semantics: absent = keep saved key, "" = clear, else replace.
    if (form.clearKey) request.api_key = "";
    else if (form.apiKey) request.api_key = form.apiKey;
    return request;
  };

  const runTest = async () => {
    setTest({ status: "testing" });
    try {
      const result = await api.testRefinerSettings(body());
      setTest({ status: "done", result });
    } catch (error) {
      setTest({ status: "error", message: (error as Error).message });
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await api.updateRefinerSettings(body());
      toast.success(t("system.refinerSettingsSaved"));
      setForm({ ...form, apiKey: "", clearKey: false });
      void queryClient.invalidateQueries({ queryKey: ["settings", "refiner"] });
      void queryClient.invalidateQueries({ queryKey: ["system", "health"] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setSaving(false);
    }
  };

  const testResult = test.status === "done" ? test.result : null;

  return (
    <Card title={t("system.refinerSettingsTitle")}>
      <p style={{ marginTop: 0, color: "var(--ink-2)", fontSize: 13 }}>
        {t("system.refinerSettingsIntro")}
      </p>
      <div className="form-grid">
        <div className="field">
          <label htmlFor="refiner-provider">{t("system.refinerProvider")}</label>
          <select
            id="refiner-provider"
            value={form.provider}
            onChange={(e) => set({ provider: e.target.value as RefinerProvider })}
          >
            <option value="off">{t("system.refinerProviderOff")}</option>
            <option value="anthropic">{t("system.refinerProviderAnthropic")}</option>
            <option value="openai_compatible">{t("system.refinerProviderLocal")}</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="refiner-model">{t("system.refinerModel")}</label>
          <input
            id="refiner-model"
            value={form.model}
            placeholder={MODEL_PLACEHOLDER[form.provider]}
            onChange={(e) => set({ model: e.target.value })}
          />
        </div>
        {form.provider === "openai_compatible" && (
          <div className="field">
            <label htmlFor="refiner-base-url">{t("system.refinerBaseUrl")}</label>
            <input
              id="refiner-base-url"
              value={form.base_url}
              placeholder={OLLAMA_BASE_URL}
              onChange={(e) => set({ base_url: e.target.value })}
            />
            <span className="btn-row">
              <button
                type="button"
                className="btn sm"
                onClick={() => set({ base_url: OLLAMA_BASE_URL })}
              >
                {t("system.refinerFillOllama")}
              </button>
              <button
                type="button"
                className="btn sm"
                onClick={() => set({ base_url: LMSTUDIO_BASE_URL })}
              >
                {t("system.refinerFillLmStudio")}
              </button>
            </span>
          </div>
        )}
        {form.provider !== "off" && (
          <div className="field">
            <label htmlFor="refiner-api-key">{t("system.refinerApiKey")}</label>
            <input
              id="refiner-api-key"
              type="password"
              autoComplete="off"
              value={form.apiKey}
              placeholder={
                settings.has_api_key && !form.clearKey
                  ? t("system.refinerApiKeySaved")
                  : form.provider === "anthropic"
                    ? "sk-ant-…"
                    : ""
              }
              onChange={(e) => set({ apiKey: e.target.value, clearKey: false })}
            />
            <span className="hint">
              {form.clearKey
                ? t("system.refinerApiKeyClearPending")
                : settings.has_api_key
                  ? t("system.refinerApiKeyKeepHint")
                  : ""}
            </span>
            {settings.has_api_key && !form.clearKey && (
              <button
                type="button"
                className="btn sm"
                onClick={() => set({ apiKey: "", clearKey: true })}
              >
                {t("system.refinerApiKeyClear")}
              </button>
            )}
          </div>
        )}
        <div className="field">
          <label htmlFor="refiner-confidence">{t("system.refinerConfidence")}</label>
          <input
            id="refiner-confidence"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={form.confidence}
            onChange={(e) => set({ confidence: e.target.value })}
          />
          <span className="hint">{t("system.refinerConfidenceHint")}</span>
        </div>
        <div className="field field-check">
          <input
            id="refiner-match-menu"
            type="checkbox"
            checked={form.matchMenuItems}
            onChange={(e) => set({ matchMenuItems: e.target.checked })}
          />
          <label htmlFor="refiner-match-menu">{t("system.refinerMatchMenu")}</label>
        </div>
        <div className="field">
          <label htmlFor="refiner-rate">{t("system.refinerMaxPerMinute")}</label>
          <input
            id="refiner-rate"
            type="number"
            min={0}
            step={1}
            value={form.maxPerMinute}
            onChange={(e) => set({ maxPerMinute: e.target.value })}
          />
        </div>
      </div>

      <div className="btn-row" style={{ marginTop: 12 }}>
        <button
          className="btn"
          disabled={test.status === "testing" || form.provider === "off"}
          onClick={() => void runTest()}
        >
          {test.status === "testing" ? t("system.refinerTesting") : t("system.refinerTestButton")}
        </button>
        <button className="btn primary" disabled={saving} onClick={() => void save()}>
          {saving ? "…" : t("common.save")}
        </button>
      </div>

      {testResult?.ok && testResult.reply && (
        <div
          className="wizard-test-ok"
          style={{
            marginTop: 12,
            background: "var(--good-soft)",
            borderRadius: "var(--radius-sm)",
            padding: 12,
          }}
        >
          <Badge tone="good">{t("system.refinerTestOk")}</Badge>
          <p className="hint" style={{ margin: 0 }}>
            {t("system.refinerTestLatency", { ms: testResult.latency_ms ?? 0 })}
          </p>
          <dl className="kv" style={{ margin: 0 }}>
            <dt>{t("common.category")}</dt>
            <dd>{testResult.reply.category_key ?? "—"}</dd>
            <dt>{t("common.item")}</dt>
            <dd>{testResult.reply.menu_item_name ?? "—"}</dd>
            <dt>{t("common.confidence")}</dt>
            <dd>{Math.round(testResult.reply.confidence * 100)}%</dd>
            <dt>{t("system.refinerTestIsItem")}</dt>
            <dd>{testResult.reply.is_item ? t("common.yes") : t("common.no")}</dd>
          </dl>
        </div>
      )}
      {(testResult?.ok === false || test.status === "error") && (
        <div
          className="wizard-test-fail"
          style={{
            marginTop: 12,
            background: "var(--critical-soft)",
            borderRadius: "var(--radius-sm)",
            padding: 12,
          }}
        >
          <Badge tone="bad">{t("system.refinerTestFail")}</Badge>
          <p className="hint" style={{ margin: 0 }}>
            {test.status === "error" ? test.message : testResult?.error}
          </p>
        </div>
      )}
    </Card>
  );
}
