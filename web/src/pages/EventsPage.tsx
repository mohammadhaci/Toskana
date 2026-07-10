import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { mediaUrl } from "../api/client";
import {
  useActiveRestaurantId,
  useCameras,
  useCategories,
  useEventMutations,
  useEvents,
} from "../api/hooks";
import type { Direction, EventRecord } from "../api/types";
import { useToast } from "../components/Toast";
import { Badge, Card, ColorChip, EmptyState, LoadError, Pagination, Skeleton } from "../components/ui";
import {
  defaultEventFilters,
  filtersToQuery,
  localInputToMs,
  msToLocalInput,
  type EventFilters,
} from "../lib/eventFilters";
import { categoryLabel, formatDateTime, itemLabel } from "../lib/format";

function SnapshotThumb({ rid, event }: { rid: number; event: EventRecord }) {
  const [failed, setFailed] = useState(false);
  if (!event.snapshot_path || failed) return <span style={{ color: "var(--ink-3)" }}>—</span>;
  return (
    <a href={mediaUrl.eventSnapshot(rid, event.id)} target="_blank" rel="noreferrer">
      <img
        src={mediaUrl.eventSnapshot(rid, event.id)}
        alt=""
        loading="lazy"
        onError={() => setFailed(true)}
        style={{ width: 72, height: 44, objectFit: "cover", borderRadius: 4, display: "block" }}
      />
    </a>
  );
}

export default function EventsPage() {
  const { t, i18n } = useTranslation();
  const rid = useActiveRestaurantId();
  const toast = useToast();
  const [draft, setDraft] = useState<EventFilters>(defaultEventFilters);
  const [applied, setApplied] = useState<EventFilters>(defaultEventFilters);

  const query = useMemo(() => filtersToQuery(applied), [applied]);
  const { data, isLoading, isError, refetch } = useEvents(rid, query);
  const { data: cameras } = useCameras(rid);
  const { data: categories } = useCategories(rid);
  const { setCanonical } = useEventMutations(rid, { onError: (e) => toast.error(e.message) });

  const cameraName = (id: number) => cameras?.items.find((c) => c.id === id)?.name ?? `#${id}`;
  const categoryOf = (id: number | null) =>
    id === null ? null : (categories?.items.find((c) => c.id === id) ?? null);

  const apply = () => setApplied({ ...draft, offset: 0 });
  const reset = () => {
    setDraft(defaultEventFilters);
    setApplied(defaultEventFilters);
  };

  const csvHref = rid === null ? "#" : mediaUrl.eventsCsv(rid, filtersToQuery(applied, false));

  const set = <K extends keyof EventFilters>(key: K, value: EventFilters[K]) =>
    setDraft((d) => ({ ...d, [key]: value }));

  return (
    <>
      <Card>
        <div className="form-grid">
          <div className="field">
            <label>{t("events.filter.from")}</label>
            <input
              type="datetime-local"
              value={msToLocalInput(draft.from_ts)}
              onChange={(e) => set("from_ts", localInputToMs(e.target.value))}
            />
          </div>
          <div className="field">
            <label>{t("events.filter.to")}</label>
            <input
              type="datetime-local"
              value={msToLocalInput(draft.to_ts)}
              onChange={(e) => set("to_ts", localInputToMs(e.target.value))}
            />
          </div>
          <div className="field">
            <label>{t("events.filter.camera")}</label>
            <select
              value={draft.camera_id ?? ""}
              onChange={(e) => set("camera_id", e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{t("common.all")}</option>
              {cameras?.items.map((camera) => (
                <option key={camera.id} value={camera.id}>
                  {camera.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>{t("events.filter.category")}</label>
            <select
              value={draft.category_id ?? ""}
              onChange={(e) => set("category_id", e.target.value ? Number(e.target.value) : null)}
            >
              <option value="">{t("common.all")}</option>
              {categories?.items.map((category) => (
                <option key={category.id} value={category.id}>
                  {categoryLabel(category, i18n.language)}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>{t("events.filter.direction")}</label>
            <select
              value={draft.direction ?? ""}
              onChange={(e) => set("direction", (e.target.value || null) as Direction | null)}
            >
              <option value="">{t("common.all")}</option>
              <option value="out">{t("common.out")}</option>
              <option value="in">{t("common.in")}</option>
            </select>
          </div>
          <div className="field">
            <label>{t("events.filter.query")}</label>
            <input value={draft.q} onChange={(e) => set("q", e.target.value)} placeholder={t("common.search")} />
          </div>
          <div className="field field-check">
            <input
              id="canonical-only"
              type="checkbox"
              checked={draft.canonical_only}
              onChange={(e) => set("canonical_only", e.target.checked)}
            />
            <label htmlFor="canonical-only">{t("events.filter.canonicalOnly")}</label>
          </div>
          <div className="btn-row">
            <button className="btn primary" onClick={apply}>
              {t("events.filter.apply")}
            </button>
            <button className="btn" onClick={reset}>
              {t("events.filter.reset")}
            </button>
            <a className="btn" href={csvHref} download>
              {t("events.exportCsv")}
            </a>
          </div>
        </div>
      </Card>

      <Card
        title={
          <>
            <span>{t("events.title")}</span>
            {data && <Badge>{t("events.total", { count: data.total })}</Badge>}
          </>
        }
      >
        {isLoading && <Skeleton height={20} count={8} />}
        {isError && <LoadError retry={() => void refetch()} />}
        {data && data.items.length === 0 && (
          <EmptyState icon="🧾" title={t("events.empty")} hint={t("events.emptyHint")} />
        )}
        {data && data.items.length > 0 && rid !== null && (
          <>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>{t("events.table.snapshot")}</th>
                    <th>{t("common.time")}</th>
                    <th>{t("common.camera")}</th>
                    <th>{t("events.table.item")}</th>
                    <th>{t("events.table.class")}</th>
                    <th>{t("common.direction")}</th>
                    <th className="num">{t("common.confidence")}</th>
                    <th>{t("events.table.canonical")}</th>
                    <th>{t("common.actions")}</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((event) => {
                    const category = categoryOf(event.category_id);
                    return (
                      <tr key={event.id}>
                        <td>
                          <SnapshotThumb rid={rid} event={event} />
                        </td>
                        <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(event.ts, i18n.language)}</td>
                        <td>{cameraName(event.camera_id)}</td>
                        <td>
                          <span className="btn-row">
                            <ColorChip color={category?.color_hex} />
                            {itemLabel(event, category, i18n.language, t("common.unmapped"))}
                          </span>
                        </td>
                        <td>{event.raw_class_name}</td>
                        <td>
                          <Badge tone={event.direction === "out" ? "accent" : "warn"}>
                            {event.direction === "out" ? t("common.out") : t("common.in")}
                          </Badge>
                        </td>
                        <td className="num">{event.confidence.toFixed(2)}</td>
                        <td>
                          {event.is_canonical ? (
                            <Badge tone="good">{t("common.yes")}</Badge>
                          ) : (
                            <Badge tone="bad">{t("events.suppressedBadge")}</Badge>
                          )}
                        </td>
                        <td>
                          <button
                            className="btn sm"
                            onClick={() =>
                              setCanonical.mutate({ id: event.id, isCanonical: !event.is_canonical })
                            }
                            disabled={setCanonical.isPending}
                          >
                            {event.is_canonical ? t("events.makeSuppressed") : t("events.makeCanonical")}
                          </button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <Pagination
              total={data.total}
              limit={applied.limit}
              offset={applied.offset}
              onOffset={(offset) => setApplied((a) => ({ ...a, offset }))}
            />
          </>
        )}
      </Card>
    </>
  );
}
