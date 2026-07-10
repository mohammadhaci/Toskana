import { useTranslation } from "react-i18next";

import { useSystemHealth, useSystemInfo } from "../api/hooks";
import { Badge, Card, EmptyState, Skeleton } from "../components/ui";
import { formatBytes, formatDateTime } from "../lib/format";

export default function SystemPage() {
  const { t, i18n } = useTranslation();
  const { data: health, isLoading: healthLoading } = useSystemHealth();
  const { data: info, isLoading: infoLoading } = useSystemInfo();

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
                <Badge tone={pipeline.running ? "good" : "bad"}>
                  {pipeline.running ? t("live.running") : t("live.stopped")}
                </Badge>
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
            </div>
          ))}
        </div>
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
