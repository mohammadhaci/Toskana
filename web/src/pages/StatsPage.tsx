import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useActiveRestaurantId, useCameras, useCategories, useTimeseries } from "../api/hooks";
import type { Bucket } from "../api/types";
import { Card, ColorChip, EmptyState, Skeleton } from "../components/ui";
import { localInputToMs, msToLocalInput } from "../lib/eventFilters";
import { categoryLabel } from "../lib/format";
import {
  grandTotals,
  isUngrouped,
  presetBounds,
  toStackedSeries,
  totalsByGroup,
  type RangePreset,
} from "../lib/statsTransforms";

/** Fixed categorical slots (dataviz reference palette) for series without an
 * entity color of their own (cameras). Categories use their DB color. */
const SLOT_COLORS = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"];
const UNMAPPED_COLOR = "#b3b1ab";

const CHART_INK = "#898781";
const CHART_GRID = "#e1e0d9";
const SURFACE = "#fcfcfb";

export default function StatsPage() {
  const { t, i18n } = useTranslation();
  const rid = useActiveRestaurantId();
  const [preset, setPreset] = useState<RangePreset>("today");
  const [bucket, setBucket] = useState<Bucket>("hour");
  const [customFrom, setCustomFrom] = useState<number | null>(null);
  const [customTo, setCustomTo] = useState<number | null>(null);

  const range = useMemo(() => {
    if (preset === "custom") {
      return { from_ts: customFrom ?? undefined, to_ts: customTo ?? undefined };
    }
    return presetBounds(preset);
  }, [preset, customFrom, customTo]);

  const byCategory = useTimeseries(rid, { bucket, ...range, group_by: "category" });
  const byCamera = useTimeseries(rid, { bucket, ...range, group_by: "camera" });
  const { data: categories } = useCategories(rid);
  const { data: cameras } = useCameras(rid);

  const categoryColor = (groupKey: string): string => {
    if (isUngrouped(groupKey)) return UNMAPPED_COLOR;
    const category = categories?.items.find((c) => String(c.id) === groupKey);
    return category?.color_hex ?? UNMAPPED_COLOR;
  };
  const categoryName = (groupKey: string): string => {
    if (isUngrouped(groupKey)) return t("common.unmapped");
    const category = categories?.items.find((c) => String(c.id) === groupKey);
    return category ? categoryLabel(category, i18n.language) : `#${groupKey}`;
  };
  const cameraName = (groupKey: string): string =>
    cameras?.items.find((c) => String(c.id) === groupKey)?.name ?? `#${groupKey}`;

  const stacked = useMemo(
    () => toStackedSeries(byCategory.data?.rows ?? [], "out"),
    [byCategory.data],
  );
  const cameraTotals = useMemo(() => totalsByGroup(byCamera.data?.rows ?? []), [byCamera.data]);
  const categoryTotals = useMemo(() => totalsByGroup(byCategory.data?.rows ?? []), [byCategory.data]);
  const totals = useMemo(() => grandTotals(byCategory.data?.rows ?? []), [byCategory.data]);

  const formatBucket = (ts: number): string => {
    const d = new Date(ts);
    const locale = i18n.language.startsWith("de") ? "de-AT" : "en-GB";
    return bucket === "hour"
      ? d.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString(locale, { day: "2-digit", month: "2-digit" });
  };

  const loading = byCategory.isLoading || byCamera.isLoading;
  const empty = !loading && (byCategory.data?.rows.length ?? 0) === 0;

  const donutData = categoryTotals
    .filter((row) => row.out > 0)
    .map((row) => ({ name: categoryName(row.group), value: row.out, color: categoryColor(row.group) }));

  return (
    <>
      <Card>
        <div className="btn-row" style={{ justifyContent: "space-between" }}>
          <div className="btn-row">
            {(["today", "yesterday", "last7", "custom"] as const).map((p) => (
              <button
                key={p}
                className={`btn sm ${preset === p ? "primary" : ""}`}
                onClick={() => setPreset(p)}
              >
                {t(`stats.range.${p}`)}
              </button>
            ))}
            {preset === "custom" && (
              <>
                <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12 }}>{t("stats.range.from")}</span>
                  <input
                    className="input"
                    type="datetime-local"
                    value={msToLocalInput(customFrom)}
                    onChange={(e) => setCustomFrom(localInputToMs(e.target.value))}
                  />
                </label>
                <label className="field" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                  <span style={{ fontSize: 12 }}>{t("stats.range.to")}</span>
                  <input
                    className="input"
                    type="datetime-local"
                    value={msToLocalInput(customTo)}
                    onChange={(e) => setCustomTo(localInputToMs(e.target.value))}
                  />
                </label>
              </>
            )}
          </div>
          <div className="btn-row">
            <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{t("stats.bucket.label")}</span>
            {(["hour", "day"] as const).map((b) => (
              <button key={b} className={`btn sm ${bucket === b ? "primary" : ""}`} onClick={() => setBucket(b)}>
                {t(`stats.bucket.${b}`)}
              </button>
            ))}
          </div>
        </div>
      </Card>

      <div className="grid grid-tiles">
        <div className="tile" style={{ borderTopColor: "var(--accent)" }}>
          <span className="tile-label">{t("stats.totalOut")}</span>
          <span className="tile-value">{totals.out}</span>
        </div>
        <div className="tile">
          <span className="tile-label">{t("stats.totalIn")}</span>
          <span className="tile-value">{totals.in}</span>
        </div>
        <div className="tile">
          <span className="tile-label">{t("stats.totalNet")}</span>
          <span className="tile-value">{totals.net}</span>
        </div>
      </div>

      {loading && (
        <Card>
          <Skeleton height={260} />
        </Card>
      )}
      {empty && (
        <Card>
          <EmptyState icon="📉" title={t("stats.empty")} hint={t("stats.emptyHint")} />
        </Card>
      )}

      {!loading && !empty && (
        <>
          <Card title={`${t("stats.stackedTitle")} — ${t("stats.metric.out")}`}>
            <div style={{ width: "100%", height: 280 }}>
              <ResponsiveContainer>
                <AreaChart data={stacked.points} margin={{ top: 8, right: 12, left: -12, bottom: 0 }}>
                  <CartesianGrid stroke={CHART_GRID} strokeWidth={1} vertical={false} />
                  <XAxis
                    dataKey="bucket_ts"
                    tickFormatter={formatBucket}
                    tick={{ fontSize: 11, fill: CHART_INK }}
                    stroke={CHART_GRID}
                  />
                  <YAxis tick={{ fontSize: 11, fill: CHART_INK }} stroke={CHART_GRID} allowDecimals={false} />
                  <Tooltip
                    labelFormatter={(ts) => formatBucket(ts as number)}
                    contentStyle={{ fontSize: 12, borderRadius: 8 }}
                  />
                  <Legend formatter={(value) => categoryName(String(value))} wrapperStyle={{ fontSize: 12 }} />
                  {stacked.groups.map((group) => (
                    <Area
                      key={group}
                      type="monotone"
                      dataKey={group}
                      stackId="out"
                      name={group}
                      stroke={categoryColor(group)}
                      strokeWidth={2}
                      fill={categoryColor(group)}
                      fillOpacity={0.75}
                    />
                  ))}
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <div className="grid grid-2">
            <Card title={t("stats.byCameraTitle")}>
              <div style={{ width: "100%", height: 240 }}>
                <ResponsiveContainer>
                  <BarChart
                    data={cameraTotals.map((row) => ({ ...row, name: cameraName(row.group) }))}
                    margin={{ top: 8, right: 12, left: -12, bottom: 0 }}
                  >
                    <CartesianGrid stroke={CHART_GRID} strokeWidth={1} vertical={false} />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: CHART_INK }} stroke={CHART_GRID} />
                    <YAxis tick={{ fontSize: 11, fill: CHART_INK }} stroke={CHART_GRID} allowDecimals={false} />
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Bar
                      dataKey="out"
                      name={t("common.out")}
                      maxBarSize={24}
                      radius={[4, 4, 0, 0]}
                      fill={SLOT_COLORS[0]}
                    />
                    <Bar
                      dataKey="in"
                      name={t("common.in")}
                      maxBarSize={24}
                      radius={[4, 4, 0, 0]}
                      fill={SLOT_COLORS[2]}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card title={t("stats.donutTitle")}>
              <div style={{ width: "100%", height: 240 }}>
                <ResponsiveContainer>
                  <PieChart>
                    <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8 }} />
                    <Legend wrapperStyle={{ fontSize: 12 }} />
                    <Pie
                      data={donutData}
                      dataKey="value"
                      nameKey="name"
                      innerRadius="55%"
                      outerRadius="85%"
                      paddingAngle={2}
                      stroke={SURFACE}
                      strokeWidth={2}
                    >
                      {donutData.map((entry) => (
                        <Cell key={entry.name} fill={entry.color} />
                      ))}
                    </Pie>
                  </PieChart>
                </ResponsiveContainer>
              </div>
            </Card>
          </div>

          <Card title={t("stats.totalsTitle")}>
            <div className="table-wrap">
              <table className="data">
                <thead>
                  <tr>
                    <th>{t("common.category")}</th>
                    <th className="num">{t("common.out")}</th>
                    <th className="num">{t("common.in")}</th>
                    <th className="num">{t("common.net")}</th>
                  </tr>
                </thead>
                <tbody>
                  {categoryTotals.map((row) => (
                    <tr key={row.group}>
                      <td>
                        <span className="btn-row">
                          <ColorChip color={categoryColor(row.group)} />
                          {categoryName(row.group)}
                        </span>
                      </td>
                      <td className="num">{row.out}</td>
                      <td className="num">{row.in}</td>
                      <td className="num">{row.net}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot>
                  <tr>
                    <td>{t("common.total")}</td>
                    <td className="num">{totals.out}</td>
                    <td className="num">{totals.in}</td>
                    <td className="num">{totals.net}</td>
                  </tr>
                </tfoot>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}
