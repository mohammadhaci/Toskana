import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { mediaUrl } from "../../api/client";
import { useLineMutations, useLines } from "../../api/hooks";
import type { Line } from "../../api/types";
import { useToast } from "../../components/Toast";
import { Card, EmptyState, Skeleton } from "../../components/ui";
import {
  dragEndpoint,
  flipDirection,
  hitEndpoint,
  normToPx,
  outArrow,
  type Endpoint,
  type NormLine,
} from "../../lib/lineMath";

const CANVAS_ASPECT = 9 / 16;
const ACCENT = "#c05a2e";
const HANDLE_R = 8;

interface Tuning {
  name: string;
  min_track_age: number;
  hysteresis_px: number;
  cooldown_ms: number;
  count_directions: string;
  enabled: boolean;
}

function drawScene(
  canvas: HTMLCanvasElement,
  image: HTMLImageElement | null,
  line: NormLine | null,
): void {
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const { width, height } = canvas;
  ctx.clearRect(0, 0, width, height);
  if (image) {
    ctx.drawImage(image, 0, 0, width, height);
  } else {
    ctx.fillStyle = "#4a4742";
    ctx.fillRect(0, 0, width, height);
  }
  if (!line) return;

  const a = normToPx({ x: line.x1, y: line.y1 }, width, height);
  const b = normToPx({ x: line.x2, y: line.y2 }, width, height);

  // line
  ctx.strokeStyle = ACCENT;
  ctx.lineWidth = 3;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(a.x, a.y);
  ctx.lineTo(b.x, b.y);
  ctx.stroke();

  // direction arrow (positive = "out" side)
  const arrow = outArrow(line, width, height);
  if (arrow) {
    ctx.strokeStyle = "#ffffff";
    ctx.fillStyle = "#ffffff";
    ctx.lineWidth = 2.5;
    ctx.beginPath();
    ctx.moveTo(arrow.from.x, arrow.from.y);
    ctx.lineTo(arrow.to.x, arrow.to.y);
    ctx.stroke();
    const angle = Math.atan2(arrow.to.y - arrow.from.y, arrow.to.x - arrow.from.x);
    ctx.beginPath();
    ctx.moveTo(arrow.to.x, arrow.to.y);
    ctx.lineTo(arrow.to.x - 9 * Math.cos(angle - 0.45), arrow.to.y - 9 * Math.sin(angle - 0.45));
    ctx.lineTo(arrow.to.x - 9 * Math.cos(angle + 0.45), arrow.to.y - 9 * Math.sin(angle + 0.45));
    ctx.closePath();
    ctx.fill();
  }

  // endpoint handles: A = start (square-ish), B = end
  for (const [point, label] of [
    [a, "A"],
    [b, "B"],
  ] as const) {
    ctx.beginPath();
    ctx.arc(point.x, point.y, HANDLE_R, 0, Math.PI * 2);
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.strokeStyle = ACCENT;
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.fillStyle = ACCENT;
    ctx.font = "bold 11px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(label, point.x, point.y + 0.5);
  }
}

export default function LineEditorPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { id } = useParams();
  const cameraId = Number(id);

  const { data: lines, isLoading } = useLines(Number.isFinite(cameraId) ? cameraId : null);
  const { create, update, remove } = useLineMutations(cameraId, { onError: (e) => toast.error(e.message) });

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  const [imageFailed, setImageFailed] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [geom, setGeom] = useState<NormLine | null>(null);
  const [tuning, setTuning] = useState<Tuning | null>(null);
  const dragging = useRef<Endpoint | null>(null);

  const selected: Line | null = lines?.items.find((l) => l.id === selectedId) ?? null;

  // pick the first line once loaded
  useEffect(() => {
    if (lines && lines.items.length > 0 && selectedId === null) {
      setSelectedId(lines.items[0].id);
    }
  }, [lines, selectedId]);

  // sync local editable state whenever the selected server line changes
  useEffect(() => {
    if (selected) {
      setGeom({ x1: selected.x1, y1: selected.y1, x2: selected.x2, y2: selected.y2 });
      setTuning({
        name: selected.name,
        min_track_age: selected.min_track_age,
        hysteresis_px: selected.hysteresis_px,
        cooldown_ms: selected.cooldown_ms,
        count_directions: selected.count_directions,
        enabled: selected.enabled,
      });
    } else if (!isLoading) {
      setGeom(null);
      setTuning(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, selected?.id]);

  // load the snapshot
  useEffect(() => {
    const img = new Image();
    img.onload = () => setImage(img);
    img.onerror = () => setImageFailed(true);
    img.src = mediaUrl.snapshot(cameraId, Date.now());
  }, [cameraId]);

  // (re)draw
  const redraw = useCallback(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const width = wrap.clientWidth;
    const aspect = image ? image.naturalHeight / image.naturalWidth : CANVAS_ASPECT;
    canvas.width = width;
    canvas.height = Math.round(width * aspect);
    drawScene(canvas, image, geom);
  }, [image, geom]);

  useEffect(() => {
    redraw();
    window.addEventListener("resize", redraw);
    return () => window.removeEventListener("resize", redraw);
  }, [redraw]);

  const canvasPoint = (event: React.PointerEvent<HTMLCanvasElement>) => {
    const canvas = canvasRef.current!;
    const rect = canvas.getBoundingClientRect();
    return {
      x: ((event.clientX - rect.left) / rect.width) * canvas.width,
      y: ((event.clientY - rect.top) / rect.height) * canvas.height,
    };
  };

  const onPointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!geom || !canvasRef.current) return;
    const point = canvasPoint(event);
    const hit = hitEndpoint(geom, point, canvasRef.current.width, canvasRef.current.height, 16);
    if (hit) {
      dragging.current = hit;
      event.currentTarget.setPointerCapture(event.pointerId);
    }
  };

  const onPointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
    if (!dragging.current || !geom || !canvasRef.current) return;
    const point = canvasPoint(event);
    setGeom(dragEndpoint(geom, dragging.current, point, canvasRef.current.width, canvasRef.current.height));
  };

  const onPointerUp = () => {
    dragging.current = null;
  };

  const save = () => {
    if (!geom || !tuning || selectedId === null) return;
    update.mutate(
      { id: selectedId, body: { ...geom, ...tuning } },
      { onSuccess: () => toast.success(t("admin.lines.savedHot")) },
    );
  };

  const addLine = () => {
    create.mutate(
      { name: "Line", x1: 0.5, y1: 0.15, x2: 0.5, y2: 0.85 },
      { onSuccess: (line) => setSelectedId(line.id) },
    );
  };

  return (
    <>
      <div className="btn-row">
        <Link className="btn sm" to="/admin/cameras">
          ← {t("admin.lines.back")}
        </Link>
      </div>

      <Card
        title={
          <>
            <span>{t("admin.lines.title")}</span>
            <span className="btn-row">
              {lines && lines.items.length > 1 && (
                <select
                  className="input"
                  value={selectedId ?? ""}
                  onChange={(e) => setSelectedId(Number(e.target.value))}
                >
                  {lines.items.map((line) => (
                    <option key={line.id} value={line.id}>
                      {t("admin.lines.selectLine")} {line.id}: {line.name}
                    </option>
                  ))}
                </select>
              )}
              <button className="btn sm" onClick={addLine} disabled={create.isPending}>
                {t("admin.lines.addLine")}
              </button>
            </span>
          </>
        }
      >
        {isLoading && <Skeleton height={320} />}
        {!isLoading && (!lines || lines.items.length === 0) && (
          <EmptyState icon="📐" title={t("admin.lines.noLines")} hint={t("admin.lines.noLinesHint")} />
        )}
        {!isLoading && geom && (
          <>
            <p style={{ marginTop: 0, fontSize: 13, color: "var(--ink-2)" }}>
              {t("admin.lines.instructions")}
              {imageFailed && <> — {t("admin.lines.snapshotMissing")}</>}
            </p>
            <div ref={wrapRef}>
              <canvas
                ref={canvasRef}
                className="line-editor-canvas"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
              />
            </div>
            <div className="btn-row" style={{ marginTop: 12 }}>
              <button className="btn" onClick={() => setGeom(flipDirection(geom))}>
                ⇄ {t("admin.lines.flip")}
              </button>
              <button className="btn primary" onClick={save} disabled={update.isPending}>
                {t("common.save")}
              </button>
              <button
                className="btn danger"
                disabled={remove.isPending || selectedId === null}
                onClick={() => {
                  if (selectedId !== null && window.confirm(t("common.confirmDelete"))) {
                    remove.mutate(selectedId, { onSuccess: () => setSelectedId(null) });
                  }
                }}
              >
                {t("admin.lines.deleteLine")}
              </button>
            </div>
          </>
        )}

        {tuning && (
          <div className="form-grid" style={{ marginTop: 16 }}>
            <div className="field">
              <label>{t("common.name")}</label>
              <input value={tuning.name} onChange={(e) => setTuning({ ...tuning, name: e.target.value })} />
            </div>
            <div className="field">
              <label>{t("admin.lines.minTrackAge")}</label>
              <input
                type="number"
                min={0}
                max={100}
                value={tuning.min_track_age}
                onChange={(e) => setTuning({ ...tuning, min_track_age: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>{t("admin.lines.hysteresisPx")}</label>
              <input
                type="number"
                min={0}
                max={200}
                value={tuning.hysteresis_px}
                onChange={(e) => setTuning({ ...tuning, hysteresis_px: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>{t("admin.lines.cooldownMs")}</label>
              <input
                type="number"
                min={0}
                max={60000}
                step={100}
                value={tuning.cooldown_ms}
                onChange={(e) => setTuning({ ...tuning, cooldown_ms: Number(e.target.value) })}
              />
            </div>
            <div className="field">
              <label>{t("admin.lines.countDirections")}</label>
              <select
                value={tuning.count_directions}
                onChange={(e) => setTuning({ ...tuning, count_directions: e.target.value })}
              >
                <option value="out,in">{t("admin.lines.directions.both")}</option>
                <option value="out">{t("admin.lines.directions.out")}</option>
                <option value="in">{t("admin.lines.directions.in")}</option>
              </select>
            </div>
            <div className="field field-check">
              <input
                id="line-enabled"
                type="checkbox"
                checked={tuning.enabled}
                onChange={(e) => setTuning({ ...tuning, enabled: e.target.checked })}
              />
              <label htmlFor="line-enabled">{t("common.enabled")}</label>
            </div>
          </div>
        )}
      </Card>
    </>
  );
}
