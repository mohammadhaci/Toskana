import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useSystemHealth, useSystemInfo } from "../api/hooks";
import type { CameraStatus, RetentionRunResult } from "../api/types";
import RefinerSettingsCard from "../components/RefinerSettingsCard";
import { useToast } from "../components/Toast";
import { Badge, Card, EmptyState, Skeleton } from "../components/ui";
import { formatBytes, formatDateTime } from "../lib/format";

function DriftBadge({ pipeline }: { pipeline: CameraStatus }) {
  const { t } = useTranslation();
  if (pipeline.drift_ok === null) {
    return <Badge>{t("system.driftNotCalibrated")}</Badge>;
  }
  const score = pipeline.drift_score !== null ? ` (${pipeline.drift_score.toFixed(2)})` : "";
  return pipeline.drift_ok ? (
    <Badge tone="good">
      {t("system.driftOk")}
      {score}
    </Badge>
  ) : (
    <Badge tone="bad">
      {t("system.driftAlarm")}
      {score}
    </Badge>
  );
}

export default function SystemPage() {
  const { t, i18n } = useTranslation();
  const { data: health, isLoading: healthLoading } = useSystemHealth();
  const { data: info, isLoading: infoLoading } = useSystemInfo();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [calibrating, setCalibrating] = useState<number | null>(null);
  const [retentionBusy, setRetentionBusy] = useState(false);
  const [retention, setRetention] = useState<RetentionRunResult | null>(null);

  const calibrate = async (cameraId: number) => {
    setCalibrating(cameraId);
    try {
      await api.calibrateCamera(cameraId);
      toast.success(t("system.calibrated"));
      void queryClient.invalidateQueries({ queryKey: ["system", "health"] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setCalibrating(null);
    }
  };

  const runRetention = async () => {
    setRetentionBusy(true);
    try {
      const result = await api.runRetention();
      setRetention(result);
      toast.success(
        t("system.retentionDone", {
          snapshots: result.deleted_snapshots,
          orphans: result.orphans_removed,
        }),
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setRetentionBusy(false);
    }
  };

  return (
    <>
      <div className="grid grid-tiles">
        <div className="tile" style={{ borderTopColor: health?.status === "ok" ? "var(--good)" : "var(--critical)" }}>
          <span className="tile-label">{t("system.title")}</span>
          <span className="tile-value" style={{ fontSize: 20 }}>
            {health ? (health.status === "ok" ? t("system.statusOk") : t("system.statusDegraded")) : "…"}
          </span>
        </div>
        <div className="tile">
          <span className="tile-label">{t("system.writerEvents")}</span>
          <span className="tile-value">{health?.writer_written_events ?? "…"}</span>
        </div>
        <div className="tile">
          <span className="tile-label">{t("system.writerGaps")}</span>
          <span className="tile-value">{health?.writer_written_gaps ?? "…"}</span>
        </div>
        <div className="tile">
          <span className="tile-label">{t("system.dbSize")}</span>
          <span className="tile-value" style={{ fontSize: 20 }}>
            {info ? formatBytes(info.db_size_bytes) : "…"}
          </span>
        </div>
      </div>

      <Card title={t("system.healthTitle")}>
        {healthLoading && <Skeleton height={60} count={2} />}
        {health && health.pipelines.length === 0 && (
          <EmptyState icon="🛌" title={t("system.noPipelines")} hint={t("system.noPipelinesHint")} />
        )}
        <div className="grid grid-cameras">
          {health?.pipelines.map((pipeline) => (
            <div key={pipeline.camera_id} className="card" style={{ boxShadow: "none" }}>
              <div className="cam-head">
                <h3>{pipeline.name ?? `#${pipeline.camera_id}`}</h3>
                <span className="btn-row">
                  <DriftBadge pipeline={pipeline} />
                  <Badge tone={pipeline.running ? "good" : "bad"}>
                    {pipeline.running ? t("live.running") : t("live.stopped")}
                  </Badge>
                </span>
              </div>
              <dl className="kv">
                <dt>{t("live.fps", { fps: "" }).trim()}</dt>
                <dd>{pipeline.fps.toFixed(1)}</dd>
                <dt>{t("system.frames")}</dt>
                <dd>{pipeline.frames}</dd>
                <dt>{t("system.gaps")}</dt>
                <dd>{pipeline.gaps}</dd>
                <dt>{t("system.backend")}</dt>
                <dd>{pipeline.backend ?? "—"}</dd>
                <dt>{t("system.device")}</dt>
                <dd>{pipeline.device ?? "—"}</dd>
                {pipeline.last_frame_ts !== null && (
                  <>
                    <dt>{t("common.time")}</dt>
                    <dd>{formatDateTime(pipeline.last_frame_ts, i18n.language)}</dd>
                  </>
                )}
                {pipeline.last_error && (
                  <>
                    <dt>{t("system.lastError")}</dt>
                    <dd style={{ color: "var(--critical)" }}>{pipeline.last_error}</dd>
                  </>
                )}
              </dl>
              <div className="btn-row" style={{ marginTop: 8 }}>
                <button
                  className="btn sm"
                  disabled={calibrating === pipeline.camera_id}
                  onClick={() => void calibrate(pipeline.camera_id)}
                  title={t("system.calibrateHint")}
                >
                  {calibrating === pipeline.camera_id ? "…" : t("system.calibrate")}
                </button>
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card
        title={
          <>
            <span>{t("system.refinerTitle")}</span>
            {health &&
              (health.refiner_enabled ? (
                <Badge tone="good">{t("system.refinerEnabled")}</Badge>
              ) : (
                <Badge>{t("system.refinerOff")}</Badge>
              ))}
          </>
        }
      >
        <p style={{ marginTop: 0, color: "var(--ink-2)", fontSize: 13 }}>
          {t("system.refinerIntro")}
        </p>
        {health && (
          <dl className="kv">
            <dt>{t("system.refinerProvider")}</dt>
            <dd>{health.refiner_provider ?? "off"}</dd>
            <dt>{t("system.refinerModel")}</dt>
            <dd>{health.refiner_model ?? "—"}</dd>
            <dt>{t("system.refinerRefined")}</dt>
            <dd>{health.refiner_refined ?? 0}</dd>
            <dt>{t("system.refinerFailures")}</dt>
            <dd>{health.refiner_failures ?? 0}</dd>
            <dt>{t("system.refinerSkipped")}</dt>
            <dd>{health.refiner_skipped ?? 0}</dd>
            <dt>{t("system.refinerQueue")}</dt>
            <dd>{health.refiner_queue_size ?? 0}</dd>
            {health.refiner_last_error && (
              <>
                <dt>{t("system.lastError")}</dt>
                <dd style={{ color: "var(--critical)" }}>{health.refiner_last_error}</dd>
              </>
            )}
          </dl>
        )}
      </Card>

      <RefinerSettingsCard />

      <Card
        title={
          <>
            <span>{t("system.retentionTitle")}</span>
            <button className="btn sm" disabled={retentionBusy} onClick={() => void runRetention()}>
              {retentionBusy ? t("system.retentionRunning") : t("system.retentionRun")}
            </button>
          </>
        }
      >
        <p style={{ marginTop: 0, color: "var(--ink-2)", fontSize: 13 }}>
          {t("system.retentionIntro", { days: info?.snapshot_retention_days ?? "…" })}
        </p>
        {retention && (
          <dl className="kv">
            <dt>{t("system.retentionDeleted")}</dt>
            <dd>{retention.deleted_snapshots}</dd>
            <dt>{t("system.retentionCleared")}</dt>
            <dd>{retention.cleared_events}</dd>
            <dt>{t("system.retentionOrphans")}</dt>
            <dd>{retention.orphans_removed}</dd>
            <dt>{t("system.retentionRuns")}</dt>
            <dd>{retention.runs}</dd>
          </dl>
        )}
      </Card>

      <Card title={t("system.infoTitle")}>
        {infoLoading && <Skeleton height={18} count={6} />}
        {info && (
          <dl className="kv">
            <dt>{t("system.version")}</dt>
            <dd>{info.version}</dd>
            <dt>{t("nav.restaurants")}</dt>
            <dd>
              {info.active_restaurant?.name ?? "—"} ({info.active_restaurant_slug})
            </dd>
            <dt>{t("system.dbPath")}</dt>
            <dd>{info.db_path}</dd>
            <dt>{t("system.dbSize")}</dt>
            <dd>{formatBytes(info.db_size_bytes)}</dd>
            <dt>{t("system.detector")}</dt>
            <dd>{info.detector_backend}</dd>
            <dt>{t("system.device")}</dt>
            <dd>{info.device}</dd>
            <dt>{t("system.torch")}</dt>
            <dd>{info.torch_available ? t("common.yes") : t("common.no")}</dd>
            <dt>{t("system.cuda")}</dt>
            <dd>{info.cuda_available ? t("common.yes") : t("common.no")}</dd>
            <dt>{t("system.loopFiles")}</dt>
            <dd>{info.loop_file_sources ? t("common.yes") : t("common.no")}</dd>
            <dt>{t("system.snapshotsDir")}</dt>
            <dd>{info.snapshots_dir}</dd>
          </dl>
        )}
      </Card>
    </>
  );
}
