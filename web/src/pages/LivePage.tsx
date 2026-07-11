import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { mediaUrl } from "../api/client";
import { useActiveRestaurantId, useCameraStatus, useCameras, useSystemInfo } from "../api/hooks";
import { useLiveSocket } from "../api/useLiveSocket";
import type { Camera, LiveCounter } from "../api/types";
import { Badge, Card, ColorChip, EmptyState, Skeleton } from "../components/ui";
import { categoryLabel, formatTime } from "../lib/format";

function CameraCard({ camera, onExpand }: { camera: Camera; onExpand: (camera: Camera) => void }) {
  const { t } = useTranslation();
  const { data: status } = useCameraStatus(camera.id);
  const [streamFailed, setStreamFailed] = useState(false);
  const [snapshotFailed, setSnapshotFailed] = useState(false);
  const running = status?.running ?? false;

  const showStream = running && !streamFailed;
  const showSnapshot = !showStream && !snapshotFailed;
  const expandable = showStream || showSnapshot;

  return (
    <Card>
      <div className="cam-head">
        <h3>{camera.name}</h3>
        <span className="btn-row">
          {status && running && status.fps > 0 && (
            <Badge>{t("live.fps", { fps: status.fps.toFixed(1) })}</Badge>
          )}
          <Badge tone={running ? "good" : "bad"}>{running ? t("live.running") : t("live.stopped")}</Badge>
          {expandable && (
            <button
              className="btn sm"
              title={t("live.expand")}
              aria-label={t("live.expand")}
              onClick={() => onExpand(camera)}
            >
              ⛶
            </button>
          )}
        </span>
      </div>
      <div
        className={`cam-frame ${expandable ? "cam-frame-clickable" : ""}`}
        role={expandable ? "button" : undefined}
        tabIndex={expandable ? 0 : undefined}
        title={expandable ? t("live.expand") : undefined}
        onClick={expandable ? () => onExpand(camera) : undefined}
        onKeyDown={
          expandable
            ? (e) => {
                if (e.key === "Enter" || e.key === " ") onExpand(camera);
              }
            : undefined
        }
      >
        {showStream && (
          <img src={mediaUrl.stream(camera.id)} alt={camera.name} onError={() => setStreamFailed(true)} />
        )}
        {showSnapshot && (
          <>
            <img
              src={mediaUrl.snapshot(camera.id, Date.now())}
              alt={camera.name}
              onError={() => setSnapshotFailed(true)}
            />
            <div className="cam-note">{t("live.snapshotFallback")}</div>
          </>
        )}
        {!showStream && !showSnapshot && <div className="cam-offline">{t("live.streamUnavailable")}</div>}
      </div>
    </Card>
  );
}

function CameraLightbox({ camera, onClose }: { camera: Camera; onClose: () => void }) {
  const { t } = useTranslation();
  const { data: status } = useCameraStatus(camera.id);
  const [streamFailed, setStreamFailed] = useState(false);
  const running = status?.running ?? false;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <div className="lightbox" role="dialog" aria-modal="true" aria-label={camera.name} onClick={onClose}>
      <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
        <div className="lightbox-head">
          <h3>{camera.name}</h3>
          <span className="btn-row">
            {status && running && status.fps > 0 && (
              <Badge>{t("live.fps", { fps: status.fps.toFixed(1) })}</Badge>
            )}
            <Badge tone={running ? "good" : "bad"}>
              {running ? t("live.running") : t("live.stopped")}
            </Badge>
            <button className="btn sm" aria-label={t("live.close")} onClick={onClose}>
              ✕ {t("live.close")}
            </button>
          </span>
        </div>
        {running && !streamFailed ? (
          <img
            className="lightbox-stream"
            src={mediaUrl.stream(camera.id)}
            alt={camera.name}
            onError={() => setStreamFailed(true)}
          />
        ) : (
          <img
            className="lightbox-stream"
            src={mediaUrl.snapshot(camera.id, Date.now())}
            alt={camera.name}
          />
        )}
      </div>
    </div>
  );
}

function CounterTiles({ counters, ready }: { counters: LiveCounter[]; ready: boolean }) {
  const { t, i18n } = useTranslation();
  if (!ready) {
    return (
      <div className="grid grid-tiles">
        {[0, 1, 2].map((i) => (
          <div key={i} className="tile">
            <Skeleton height={12} width="60%" />
            <Skeleton height={30} width="40%" />
          </div>
        ))}
      </div>
    );
  }
  const visible = counters.filter((c) => c.out > 0 || c.in > 0 || c.category_id !== null);
  if (visible.length === 0) {
    return <EmptyState icon="🕰️" title={t("live.noCounters")} />;
  }
  const totalOut = counters.reduce((sum, c) => sum + c.out, 0);
  const totalIn = counters.reduce((sum, c) => sum + c.in, 0);
  return (
    <div className="grid grid-tiles">
      <div className="tile" style={{ borderTopColor: "var(--accent)" }}>
        <span className="tile-label">{t("common.total")}</span>
        <span className="tile-value">{totalOut - totalIn}</span>
        <span className="tile-sub">
          <span>
            {t("common.out")} <b>{totalOut}</b>
          </span>
          <span>
            {t("common.in")} <b>{totalIn}</b>
          </span>
        </span>
      </div>
      {visible.map((counter) => (
        <div
          key={counter.category_id ?? "unmapped"}
          className="tile"
          style={{ borderTopColor: counter.color_hex ?? "var(--hairline)" }}
        >
          <span className="tile-label">
            <ColorChip color={counter.color_hex} />
            {counter.category_id === null
              ? t("common.unmapped")
              : categoryLabel(counter, i18n.language)}
          </span>
          <span className="tile-value">{counter.net}</span>
          <span className="tile-sub">
            <span>
              {t("common.out")} <b>{counter.out}</b>
            </span>
            <span>
              {t("common.in")} <b>{counter.in}</b>
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

export default function LivePage() {
  const { t, i18n } = useTranslation();
  const rid = useActiveRestaurantId();
  const { isLoading: infoLoading } = useSystemInfo();
  const { data: cameras, isLoading } = useCameras(rid);
  const live = useLiveSocket();
  const [expanded, setExpanded] = useState<Camera | null>(null);

  const counterByCategory = useMemo(() => {
    const map = new Map<number | null, LiveCounter>();
    for (const counter of live.counters) map.set(counter.category_id, counter);
    return map;
  }, [live.counters]);

  const cameraName = (id: number) => cameras?.items.find((c) => c.id === id)?.name ?? `#${id}`;

  if (!infoLoading && rid === null) {
    return <EmptyState icon="🏚️" title={t("app.noRestaurant")} hint={t("app.noRestaurantHint")} />;
  }

  return (
    <>
      {!live.connected && live.ready && (
        <div className="toast error" style={{ position: "static" }}>
          {t("live.wsReconnecting")}
        </div>
      )}

      <CounterTiles counters={live.counters} ready={live.ready} />

      <div className="grid grid-charts">
        <div>
          {isLoading && <Skeleton height={220} count={1} />}
          {cameras && cameras.items.length === 0 && (
            <Card>
              <EmptyState icon="🎥" title={t("live.noCameras")} hint={t("live.noCamerasHint")} />
            </Card>
          )}
          <div className="grid grid-cameras">
            {cameras?.items.map((camera) => (
              <CameraCard key={camera.id} camera={camera} onExpand={setExpanded} />
            ))}
          </div>
        </div>

        <Card title={t("live.tickerTitle")}>
          {live.events.length === 0 ? (
            <EmptyState icon="🫙" title={t("live.tickerEmpty")} />
          ) : (
            <div className="ticker">
              {live.events.map((event) => {
                const counter = counterByCategory.get(event.category_id ?? null);
                return (
                  <div key={event.id} className="ticker-row">
                    <ColorChip color={counter?.color_hex} />
                    <span>
                      {event.menu_item_name
                        ? event.menu_item_name
                        : event.category_id === null
                          ? (event.raw_class_name || t("common.unmapped"))
                          : counter
                            ? categoryLabel(counter, i18n.language)
                            : event.raw_class_name}
                    </span>
                    <Badge tone={event.direction === "out" ? "accent" : "warn"}>
                      {event.direction === "out" ? t("common.out") : t("common.in")}
                    </Badge>
                    <Badge>{cameraName(event.camera_id)}</Badge>
                    <time>{formatTime(event.ts, i18n.language)}</time>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </div>

      {expanded && <CameraLightbox camera={expanded} onClose={() => setExpanded(null)} />}
    </>
  );
}
