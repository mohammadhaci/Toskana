/** Guided "Add camera" wizard: type → details → connection test → save.
 *
 * RTSP vendor presets come from GET /api/camera-presets; the real URL (with
 * the password) is built and probed server-side via POST
 * .../cameras/test-source — the browser only renders a masked preview.
 * Saving is gated on a successful test for live sources (rtsp/usb); a video
 * file may be saved untested.
 */

import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { api } from "../../api/client";
import { useCameraMutations, useCameraPresets } from "../../api/hooks";
import type {
  Camera,
  CameraPreset,
  CameraTestResult,
  CameraTestSourceRequest,
  ExitGroup,
  SourceType,
} from "../../api/types";
import { useToast } from "../../components/Toast";
import { Badge, Card } from "../../components/ui";

export const PASSWORD_MASK = "•••";

/** Mask the password of `scheme://user:password@…` for display. */
export function maskRtspPassword(url: string): string {
  return url.replace(/^([a-z][a-z0-9+.-]*:\/\/)([^/@]*):([^/@]*)@/i, `$1$2:${PASSWORD_MASK}@`);
}

export interface PresetFields {
  username: string;
  password: string;
  ip: string;
  port: number;
  channel: number;
}

/** Render a preset template as a masked preview (mirrors the server builder:
 * empty credentials drop the `user:password@` part; the password is never
 * shown). Handles the `{channel:02d}` (Reolink) and `{channel}01` (Hikvision)
 * channel spellings. */
export function previewUrl(template: string, fields: PresetFields): string {
  let out = template;
  if (!fields.username && !fields.password) out = out.replace("{user}:{password}@", "");
  return out
    .replace("{user}", encodeURIComponent(fields.username))
    .replace("{password}", fields.password ? PASSWORD_MASK : "")
    .replace("{ip}", fields.ip.trim() || "…")
    .replace("{port}", String(fields.port))
    .replace("{channel:02d}", String(fields.channel).padStart(2, "0"))
    .replace("{channel}", String(fields.channel));
}

type Step = 1 | 2 | 3 | 4;
type TestState =
  | { status: "idle" }
  | { status: "testing" }
  | { status: "done"; result: CameraTestResult }
  | { status: "error"; message: string };

const MANUAL_KEY = "generic";

export default function CameraWizard({ rid, exitGroups }: { rid: number; exitGroups: ExitGroup[] }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { data: presets } = useCameraPresets();
  const { create } = useCameraMutations(rid, { onError: (e) => toast.error(e.message) });

  const [step, setStep] = useState<Step>(1);
  const [sourceType, setSourceType] = useState<SourceType | null>(null);
  const [presetKey, setPresetKey] = useState("hikvision");
  const [ip, setIp] = useState("");
  const [port, setPort] = useState(554);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [channel, setChannel] = useState(1);
  const [manualUrl, setManualUrl] = useState("");
  const [usbIndex, setUsbIndex] = useState("0");
  const [filePath, setFilePath] = useState("");
  const [test, setTest] = useState<TestState>({ status: "idle" });
  const [name, setName] = useState("");
  const [exitGroupId, setExitGroupId] = useState<number | null>(null);
  const [saved, setSaved] = useState<Camera | null>(null);
  const [started, setStarted] = useState(false);

  const preset: CameraPreset | undefined = useMemo(
    () => presets?.find((entry) => entry.key === presetKey),
    [presets, presetKey],
  );
  const isManual = presetKey === MANUAL_KEY || !preset?.url_template;
  const testResult = test.status === "done" ? test.result : null;

  const presetLabel = (entry: CameraPreset) =>
    t(`admin.cameras.wizard.presets.${entry.key}`, { defaultValue: entry.label });

  /** Any detail change invalidates a previous test result. */
  function touch<T>(setter: (value: T) => void) {
    return (value: T) => {
      setter(value);
      setTest({ status: "idle" });
    };
  }
  const setPresetKeyT = (key: string) => {
    setPresetKey(key);
    const next = presets?.find((entry) => entry.key === key);
    if (next) setPort(next.default_port);
    setTest({ status: "idle" });
  };

  const maskedPreview =
    sourceType === "rtsp"
      ? isManual
        ? maskRtspPassword(manualUrl)
        : preset?.url_template
          ? previewUrl(preset.url_template, { username, password, ip, port, channel })
          : ""
      : "";

  function testRequestBody(): CameraTestSourceRequest {
    if (sourceType === "rtsp" && !isManual) {
      return { preset_key: presetKey, ip, username, password, port, channel };
    }
    if (sourceType === "rtsp") return { source_type: "rtsp", source_url: manualUrl };
    if (sourceType === "usb") return { source_type: "usb", source_url: usbIndex };
    return { source_type: "file", source_url: filePath };
  }

  const detailsComplete =
    sourceType === "rtsp"
      ? isManual
        ? manualUrl.trim().length > 0
        : ip.trim().length > 0 && port >= 1 && port <= 65535
      : sourceType === "usb"
        ? true
        : filePath.trim().length > 0;

  // Live sources must pass the connection test; a file only needs a path.
  const canFinish = sourceType === "file" ? detailsComplete : testResult?.ok === true;

  async function runTest() {
    setTest({ status: "testing" });
    try {
      const result = await api.testCameraSource(rid, testRequestBody());
      setTest({ status: "done", result });
    } catch (error) {
      setTest({ status: "error", message: (error as Error).message });
    }
  }

  function savedSourceUrl(): string {
    if (testResult?.ok && testResult.source_url) return testResult.source_url;
    if (sourceType === "usb") return usbIndex;
    if (sourceType === "file") return filePath;
    return manualUrl;
  }

  function save() {
    create.mutate(
      {
        name: name.trim(),
        source_type: sourceType as SourceType,
        source_url: savedSourceUrl(),
        exit_group_id: exitGroupId,
      },
      {
        onSuccess: (camera) => {
          setSaved(camera);
          toast.success(t("common.saved"));
        },
      },
    );
  }

  function reset() {
    setStep(1);
    setSourceType(null);
    setIp("");
    setUsername("");
    setPassword("");
    setChannel(1);
    setManualUrl("");
    setUsbIndex("0");
    setFilePath("");
    setTest({ status: "idle" });
    setName("");
    setExitGroupId(null);
    setSaved(null);
    setStarted(false);
  }

  const stepLabels = [
    t("admin.cameras.wizard.stepType"),
    t("admin.cameras.wizard.stepDetails"),
    t("admin.cameras.wizard.stepTest"),
    t("admin.cameras.wizard.stepFinish"),
  ];

  if (saved) {
    return (
      <Card title={t("admin.cameras.wizard.title")}>
        <div className="wizard-done">
          <Badge tone="good">{t("admin.cameras.wizard.savedTitle", { name: saved.name })}</Badge>
          <p className="hint">{t("admin.cameras.wizard.savedHint")}</p>
          <div className="btn-row">
            <Link className="btn primary" to={`/admin/cameras/${saved.id}/lines`}>
              {t("admin.cameras.wizard.drawLine")}
            </Link>
            <button
              className="btn"
              disabled={started}
              onClick={() => {
                void api
                  .startCamera(saved.id)
                  .then(() => {
                    setStarted(true);
                    toast.success(t("admin.cameras.wizard.started"));
                  })
                  .catch((error: Error) => toast.error(error.message));
              }}
            >
              {started ? t("admin.cameras.wizard.started") : t("admin.cameras.wizard.startCamera")}
            </button>
            <button className="btn" onClick={reset}>
              {t("admin.cameras.wizard.addAnother")}
            </button>
          </div>
        </div>
      </Card>
    );
  }

  return (
    <Card title={t("admin.cameras.wizard.title")}>
      <ol className="wizard-steps">
        {stepLabels.map((label, index) => {
          const number = (index + 1) as Step;
          return (
            <li key={label} className={number === step ? "active" : number < step ? "done" : ""}>
              <span className="wizard-step-num">{number}</span> {label}
            </li>
          );
        })}
      </ol>

      {step === 1 && (
        <div className="wizard-type-grid">
          {(
            [
              ["rtsp", t("admin.cameras.wizard.typeRtsp"), t("admin.cameras.wizard.typeRtspHint"), "📷"],
              ["usb", t("admin.cameras.wizard.typeUsb"), t("admin.cameras.wizard.typeUsbHint"), "🔌"],
              ["file", t("admin.cameras.wizard.typeFile"), t("admin.cameras.wizard.typeFileHint"), "🎞️"],
            ] as const
          ).map(([type, label, hint, icon]) => (
            <button
              key={type}
              type="button"
              className={`wizard-type-btn ${sourceType === type ? "selected" : ""}`}
              onClick={() => {
                setSourceType(type);
                setTest({ status: "idle" });
                setStep(2);
              }}
            >
              <span className="wizard-type-icon" aria-hidden="true">
                {icon}
              </span>
              <strong>{label}</strong>
              <span className="hint">{hint}</span>
            </button>
          ))}
        </div>
      )}

      {step === 2 && sourceType === "rtsp" && (
        <>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="wiz-vendor">{t("admin.cameras.wizard.vendor")}</label>
              <select id="wiz-vendor" value={presetKey} onChange={(e) => setPresetKeyT(e.target.value)}>
                {(presets ?? []).map((entry) => (
                  <option key={entry.key} value={entry.key}>
                    {presetLabel(entry)}
                  </option>
                ))}
              </select>
            </div>
            {isManual ? (
              <div className="field">
                <label htmlFor="wiz-url">{t("admin.cameras.wizard.urlManual")}</label>
                <input
                  id="wiz-url"
                  value={manualUrl}
                  onChange={(e) => touch(setManualUrl)(e.target.value)}
                  placeholder="rtsp://user:passwort@192.168.1.20:554/…"
                />
              </div>
            ) : (
              <>
                <div className="field">
                  <label htmlFor="wiz-ip">{t("admin.cameras.wizard.ip")}</label>
                  <input
                    id="wiz-ip"
                    value={ip}
                    onChange={(e) => touch(setIp)(e.target.value)}
                    placeholder="192.168.1.20"
                  />
                </div>
                <div className="field">
                  <label htmlFor="wiz-port">{t("admin.cameras.wizard.port")}</label>
                  <input
                    id="wiz-port"
                    type="number"
                    min={1}
                    max={65535}
                    value={port}
                    onChange={(e) => touch(setPort)(Number(e.target.value))}
                  />
                </div>
                <div className="field">
                  <label htmlFor="wiz-user">{t("admin.cameras.wizard.username")}</label>
                  <input
                    id="wiz-user"
                    value={username}
                    autoComplete="off"
                    onChange={(e) => touch(setUsername)(e.target.value)}
                  />
                </div>
                <div className="field">
                  <label htmlFor="wiz-pass">{t("admin.cameras.wizard.password")}</label>
                  <input
                    id="wiz-pass"
                    type="password"
                    value={password}
                    autoComplete="new-password"
                    onChange={(e) => touch(setPassword)(e.target.value)}
                  />
                </div>
                {preset?.needs_channel && (
                  <div className="field">
                    <label htmlFor="wiz-channel">{t("admin.cameras.wizard.channel")}</label>
                    <input
                      id="wiz-channel"
                      type="number"
                      min={1}
                      max={64}
                      value={channel}
                      onChange={(e) => touch(setChannel)(Math.max(1, Number(e.target.value)))}
                    />
                    <span className="hint">{t("admin.cameras.wizard.channelHint")}</span>
                  </div>
                )}
              </>
            )}
          </div>
          {maskedPreview && (
            <p className="wizard-url-preview">
              <span>{t("admin.cameras.wizard.urlPreview")}</span> <code>{maskedPreview}</code>
            </p>
          )}
        </>
      )}

      {step === 2 && sourceType === "usb" && (
        <div className="form-grid">
          <div className="field">
            <label htmlFor="wiz-usb">{t("admin.cameras.wizard.usbIndex")}</label>
            <select id="wiz-usb" value={usbIndex} onChange={(e) => touch(setUsbIndex)(e.target.value)}>
              {["0", "1", "2"].map((index) => (
                <option key={index} value={index}>
                  {index}
                </option>
              ))}
            </select>
            <span className="hint">{t("admin.cameras.wizard.usbIndexHint")}</span>
          </div>
        </div>
      )}

      {step === 2 && sourceType === "file" && (
        <div className="form-grid">
          <div className="field">
            <label htmlFor="wiz-file">{t("admin.cameras.wizard.filePath")}</label>
            <input
              id="wiz-file"
              value={filePath}
              onChange={(e) => touch(setFilePath)(e.target.value)}
              placeholder="./data/videos/mittagsservice.mp4"
            />
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="wizard-test">
          <button
            className="btn primary"
            disabled={test.status === "testing"}
            onClick={() => void runTest()}
          >
            {test.status === "testing"
              ? t("admin.cameras.wizard.testing")
              : t("admin.cameras.wizard.testButton")}
          </button>
          {testResult?.ok && (
            <div className="wizard-test-ok">
              <Badge tone="good">{t("admin.cameras.wizard.testOk")}</Badge>
              <p className="hint">
                {t("admin.cameras.wizard.resolution")}: {testResult.width}×{testResult.height}
                {testResult.fps ? ` · ${Math.round(testResult.fps)} FPS` : ""}
              </p>
              {testResult.snapshot_b64 && (
                <img
                  className="wizard-snapshot"
                  src={`data:image/jpeg;base64,${testResult.snapshot_b64}`}
                  alt={t("admin.cameras.wizard.snapshotAlt")}
                />
              )}
            </div>
          )}
          {(testResult?.ok === false || test.status === "error") && (
            <div className="wizard-test-fail">
              <Badge tone="bad">{t("admin.cameras.wizard.testFailed")}</Badge>
              <p className="hint">
                {test.status === "error" ? test.message : testResult?.error}
              </p>
              {testResult?.source_url_masked && <code>{testResult.source_url_masked}</code>}
              <ul className="hint">
                <li>{t("admin.cameras.wizard.hintIp")}</li>
                <li>{t("admin.cameras.wizard.hintCredentials")}</li>
                <li>{t("admin.cameras.wizard.hintRtsp")}</li>
                <li>{t("admin.cameras.wizard.hintDocs")}</li>
              </ul>
            </div>
          )}
          {sourceType === "file" && !testResult?.ok && (
            <p className="hint">{t("admin.cameras.wizard.fileNoTest")}</p>
          )}
        </div>
      )}

      {step === 4 && (
        <div className="form-grid">
          <div className="field">
            <label htmlFor="wiz-name">{t("common.name")}</label>
            <input
              id="wiz-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("admin.cameras.wizard.namePlaceholder")}
            />
          </div>
          <div className="field">
            <label htmlFor="wiz-group">{t("admin.cameras.exitGroup")}</label>
            <select
              id="wiz-group"
              value={exitGroupId ?? ""}
              onChange={(e) => setExitGroupId(e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{t("admin.cameras.noExitGroup")}</option>
              {exitGroups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

      <div className="btn-row" style={{ marginTop: 16 }}>
        {step > 1 && (
          <button className="btn" onClick={() => setStep((step - 1) as Step)}>
            {t("admin.cameras.wizard.back")}
          </button>
        )}
        {step === 2 && (
          <button className="btn primary" disabled={!detailsComplete} onClick={() => setStep(3)}>
            {t("admin.cameras.wizard.next")}
          </button>
        )}
        {step === 3 && (
          <button className="btn primary" disabled={!canFinish} onClick={() => setStep(4)}>
            {t("admin.cameras.wizard.next")}
          </button>
        )}
        {step === 4 && (
          <button
            className="btn primary"
            disabled={!name.trim() || create.isPending}
            onClick={save}
          >
            {t("common.save")}
          </button>
        )}
      </div>
    </Card>
  );
}
