import { useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { mediaUrl } from "../../api/client";
import {
  useActiveRestaurantId,
  useCameraMutations,
  useCameraStatus,
  useCameras,
  useExitGroups,
} from "../../api/hooks";
import type { Camera, ExitGroup, SourceType } from "../../api/types";
import { useToast } from "../../components/Toast";
import { Badge, Card, EmptyState, Skeleton } from "../../components/ui";
import CameraWizard from "./CameraWizard";

function CameraAdminCard({
  camera,
  exitGroups,
  rid,
}: {
  camera: Camera;
  exitGroups: ExitGroup[];
  rid: number;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { update, remove, start, stop, restart } = useCameraMutations(rid, {
    onError: (e) => toast.error(e.message),
  });
  const { data: status } = useCameraStatus(camera.id);
  const [snapshotFailed, setSnapshotFailed] = useState(false);
  const [form, setForm] = useState({
    name: camera.name,
    source_type: camera.source_type as SourceType,
    source_url: camera.source_url,
    target_fps: camera.target_fps,
    exit_group_id: camera.exit_group_id,
    is_primary_in_group: camera.is_primary_in_group,
    enabled: camera.enabled,
  });
  const running = status?.running ?? false;

  return (
    <Card>
      <div className="cam-head">
        <h3>{camera.name}</h3>
        <Badge tone={running ? "good" : "bad"}>{running ? t("live.running") : t("live.stopped")}</Badge>
      </div>
      <div className="cam-frame" style={{ aspectRatio: "16 / 9", marginBottom: 12 }}>
        {!snapshotFailed ? (
          <img
            src={mediaUrl.snapshot(camera.id, Date.now())}
            alt={camera.name}
            onError={() => setSnapshotFailed(true)}
          />
        ) : (
          <div className="cam-offline">{t("admin.cameras.snapshotUnavailable")}</div>
        )}
      </div>
      <div className="form-grid">
        <div className="field">
          <label>{t("common.name")}</label>
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
        </div>
        <div className="field">
          <label>{t("admin.cameras.sourceType")}</label>
          <select
            value={form.source_type}
            onChange={(e) => setForm({ ...form, source_type: e.target.value as SourceType })}
          >
            <option value="rtsp">RTSP</option>
            <option value="usb">USB</option>
            <option value="file">File</option>
          </select>
        </div>
        <div className="field">
          <label>{t("admin.cameras.sourceUrl")}</label>
          <input value={form.source_url} onChange={(e) => setForm({ ...form, source_url: e.target.value })} />
        </div>
        <div className="field">
          <label>{t("admin.cameras.targetFps")}</label>
          <input
            type="number"
            min={1}
            max={120}
            value={form.target_fps}
            onChange={(e) => setForm({ ...form, target_fps: Number(e.target.value) })}
          />
        </div>
        <div className="field">
          <label>{t("admin.cameras.exitGroup")}</label>
          <select
            value={form.exit_group_id ?? ""}
            onChange={(e) =>
              setForm({ ...form, exit_group_id: e.target.value ? Number(e.target.value) : null })
            }
          >
            <option value="">{t("admin.cameras.noExitGroup")}</option>
            {exitGroups.map((group) => (
              <option key={group.id} value={group.id}>
                {group.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field field-check">
          <input
            id={`primary-${camera.id}`}
            type="checkbox"
            checked={form.is_primary_in_group}
            onChange={(e) => setForm({ ...form, is_primary_in_group: e.target.checked })}
          />
          <label htmlFor={`primary-${camera.id}`}>{t("admin.cameras.primaryInGroup")}</label>
        </div>
        <div className="field field-check">
          <input
            id={`enabled-${camera.id}`}
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          />
          <label htmlFor={`enabled-${camera.id}`}>{t("common.enabled")}</label>
        </div>
      </div>
      <div className="btn-row" style={{ marginTop: 12 }}>
        <button
          className="btn primary sm"
          disabled={update.isPending}
          onClick={() =>
            update.mutate(
              { id: camera.id, body: form },
              { onSuccess: () => toast.success(t("common.saved")) },
            )
          }
        >
          {t("common.save")}
        </button>
        <button className="btn sm" disabled={running || start.isPending} onClick={() => start.mutate(camera.id)}>
          {t("admin.cameras.start")}
        </button>
        <button className="btn sm" disabled={!running || stop.isPending} onClick={() => stop.mutate(camera.id)}>
          {t("admin.cameras.stop")}
        </button>
        <button className="btn sm" disabled={restart.isPending} onClick={() => restart.mutate(camera.id)}>
          {t("admin.cameras.restart")}
        </button>
        <Link className="btn sm" to={`/admin/cameras/${camera.id}/lines`}>
          {t("admin.cameras.editLines")}
        </Link>
        <button
          className="btn sm danger"
          disabled={remove.isPending}
          onClick={() => {
            if (window.confirm(t("common.confirmDelete"))) remove.mutate(camera.id);
          }}
        >
          {t("common.delete")}
        </button>
      </div>
    </Card>
  );
}

export default function CamerasPage() {
  const { t } = useTranslation();
  const rid = useActiveRestaurantId();
  const { data: cameras, isLoading } = useCameras(rid);
  const { data: exitGroups } = useExitGroups(rid);

  return (
    <>
      {isLoading && (
        <Card>
          <Skeleton height={200} />
        </Card>
      )}
      {cameras && cameras.items.length === 0 && (
        <Card>
          <EmptyState icon="🎥" title={t("admin.cameras.empty")} hint={t("admin.cameras.emptyHint")} />
        </Card>
      )}
      <div className="grid grid-cameras">
        {rid !== null &&
          cameras?.items.map((camera) => (
            <CameraAdminCard key={camera.id} camera={camera} exitGroups={exitGroups?.items ?? []} rid={rid} />
          ))}
      </div>

      {rid !== null && <CameraWizard rid={rid} exitGroups={exitGroups?.items ?? []} />}
    </>
  );
}
