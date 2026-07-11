import { useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useActiveRestaurantId, useAnalysisJobs } from "../api/hooks";
import type { AnalysisBackend, AnalysisJob, AnalysisStatus } from "../api/types";
import { useToast } from "../components/Toast";
import { Badge, Card, EmptyState, LoadError, Skeleton } from "../components/ui";
import { formatDateTime } from "../lib/format";

const DEFAULT_LINE = { x1: 0.5, y1: 0, x2: 0.5, y2: 1 };

const STATUS_TONE: Record<AnalysisStatus, "neutral" | "good" | "bad" | "warn" | "accent"> = {
  queued: "neutral",
  downloading: "accent",
  running: "accent",
  done: "good",
  error: "bad",
  cancelled: "warn",
};

const ACTIVE_STATES: AnalysisStatus[] = ["queued", "downloading", "running"];

function progressPct(job: AnalysisJob): number | null {
  if (job.status === "done") return 100;
  if (!job.frames_total || job.frames_total <= 0) return null;
  return Math.min(100, Math.round((job.frames_done / job.frames_total) * 100));
}

function CountsTable({ job }: { job: AnalysisJob }) {
  const { t } = useTranslation();
  const names = [
    ...new Set([...Object.keys(job.counts.out ?? {}), ...Object.keys(job.counts.in ?? {})]),
  ].sort();
  if (names.length === 0) return null;
  return (
    <div className="table-wrap" style={{ marginTop: 8 }}>
      <table className="data">
        <thead>
          <tr>
            <th>{t("common.category")}</th>
            <th className="num">{t("common.out")}</th>
            <th className="num">{t("common.in")}</th>
          </tr>
        </thead>
        <tbody>
          {names.map((name) => (
            <tr key={name}>
              <td>{name}</td>
              <td className="num">{job.counts.out?.[name] ?? 0}</td>
              <td className="num">{job.counts.in?.[name] ?? 0}</td>
            </tr>
          ))}
          <tr>
            <td>
              <strong>{t("common.total")}</strong>
            </td>
            <td className="num">
              <strong>{job.total_out}</strong>
            </td>
            <td className="num">
              <strong>{job.total_in}</strong>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function JobRow({ job, rid }: { job: AnalysisJob; rid: number }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const pct = progressPct(job);
  const active = ACTIVE_STATES.includes(job.status);

  const cancel = async () => {
    try {
      await api.cancelAnalysisJob(rid, job.id);
      void queryClient.invalidateQueries({ queryKey: ["analysis", rid] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <Card
      title={
        <>
          <span style={{ overflowWrap: "anywhere" }}>{job.video_name}</span>
          <span className="btn-row">
            <Badge tone={STATUS_TONE[job.status]}>{t(`analysis.status.${job.status}`)}</Badge>
            {active && (
              <button className="btn sm" onClick={() => void cancel()}>
                {t("analysis.cancel")}
              </button>
            )}
          </span>
        </>
      }
    >
      <div style={{ fontSize: 12, color: "var(--ink-3)", marginBottom: 8 }}>
        {formatDateTime(job.created_ts, i18n.language)} · {job.backend}
        {job.frames_total !== null && (
          <>
            {" "}
            · {t("analysis.frames", { done: job.frames_done, total: job.frames_total })}
          </>
        )}
      </div>
      {(active || job.status === "done") && pct !== null && (
        <div
          className="progress"
          role="progressbar"
          aria-valuenow={pct}
          aria-valuemin={0}
          aria-valuemax={100}
        >
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
      {job.error !== null && (
        <p className="var-bad" style={{ fontSize: 13, overflowWrap: "anywhere" }}>
          {job.error}
        </p>
      )}
      <CountsTable job={job} />
      {job.status === "done" && job.camera_id !== null && (
        <p style={{ marginBottom: 0 }}>
          <Link className="btn sm" to={`/events?camera_id=${job.camera_id}`}>
            {t("analysis.viewEvents")}
          </Link>
        </p>
      )}
    </Card>
  );
}

export default function AnalysisPage() {
  const { t } = useTranslation();
  const rid = useActiveRestaurantId();
  const toast = useToast();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [backend, setBackend] = useState<AnalysisBackend>("yolo");
  const [line, setLine] = useState(DEFAULT_LINE);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const { data: jobs, isLoading, isError, refetch } = useAnalysisJobs(rid);

  const canSubmit = rid !== null && !busy && (file !== null || url.trim() !== "");

  const submit = async () => {
    if (rid === null || (!file && !url.trim())) return;
    setBusy(true);
    try {
      await api.createAnalysisJob(rid, {
        file: file ?? undefined,
        url: file ? undefined : url.trim(),
        backend,
        line,
      });
      setFile(null);
      setUrl("");
      if (fileInput.current) fileInput.current.value = "";
      void queryClient.invalidateQueries({ queryKey: ["analysis", rid] });
      toast.success(t("analysis.submitted"));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) setFile(dropped);
  };

  const setCoord = (key: keyof typeof DEFAULT_LINE, value: number) =>
    setLine((current) => ({ ...current, [key]: value }));

  return (
    <>
      <Card title={t("analysis.uploadTitle")}>
        <p style={{ marginTop: 0, color: "var(--ink-2)", fontSize: 13 }}>{t("analysis.intro")}</p>
        <div className="form-grid">
          <div
            className={`dropzone ${dragOver ? "over" : ""}`}
            style={{ gridColumn: "1 / -1" }}
            onClick={() => fileInput.current?.click()}
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={onDrop}
          >
            {file ? file.name : t("analysis.dropHint")}
            <input
              ref={fileInput}
              type="file"
              accept="video/*"
              hidden
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
          <div className="field" style={{ gridColumn: "1 / -1" }}>
            <label htmlFor="analysis-url">{t("analysis.urlLabel")}</label>
            <input
              id="analysis-url"
              type="url"
              placeholder="https://www.youtube.com/watch?v=…"
              value={url}
              disabled={file !== null}
              onChange={(e) => setUrl(e.target.value)}
            />
          </div>
          <details style={{ gridColumn: "1 / -1" }}>
            <summary style={{ cursor: "pointer", color: "var(--ink-2)", fontSize: 13 }}>
              {t("analysis.advanced")}
            </summary>
            <div className="form-grid" style={{ marginTop: 12 }}>
              <div className="field">
                <label htmlFor="analysis-backend">{t("analysis.backendLabel")}</label>
                <select
                  id="analysis-backend"
                  value={backend}
                  onChange={(e) => setBackend(e.target.value as AnalysisBackend)}
                >
                  <option value="yolo">{t("analysis.backendYolo")}</option>
                  <option value="synthetic">{t("analysis.backendSynthetic")}</option>
                </select>
                {backend === "synthetic" && (
                  <p style={{ margin: "4px 0 0", fontSize: 12, color: "var(--ink-3)" }}>
                    {t("analysis.backendSyntheticHint")}
                  </p>
                )}
              </div>
              {(
                [
                  ["x1", t("analysis.line.x1")],
                  ["y1", t("analysis.line.y1")],
                  ["x2", t("analysis.line.x2")],
                  ["y2", t("analysis.line.y2")],
                ] as const
              ).map(([key, label]) => (
                <div className="field" key={key}>
                  <label htmlFor={`analysis-${key}`}>
                    {label}: {line[key].toFixed(2)}
                  </label>
                  <input
                    id={`analysis-${key}`}
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={line[key]}
                    onChange={(e) => setCoord(key, Number(e.target.value))}
                  />
                </div>
              ))}
              <p style={{ gridColumn: "1 / -1", margin: 0, fontSize: 12, color: "var(--ink-3)" }}>
                {t("analysis.line.hint")}
              </p>
            </div>
          </details>
          <div className="btn-row">
            <button className="btn primary" disabled={!canSubmit} onClick={() => void submit()}>
              {busy ? t("analysis.submitting") : t("analysis.submit")}
            </button>
          </div>
        </div>
      </Card>

      {isLoading && (
        <Card>
          <Skeleton height={20} count={4} />
        </Card>
      )}
      {isError && (
        <Card>
          <LoadError retry={() => void refetch()} />
        </Card>
      )}
      {jobs && jobs.length === 0 && (
        <Card>
          <EmptyState icon="🎬" title={t("analysis.empty")} hint={t("analysis.emptyHint")} />
        </Card>
      )}
      {jobs && rid !== null && jobs.map((job) => <JobRow key={job.id} job={job} rid={rid} />)}
    </>
  );
}
