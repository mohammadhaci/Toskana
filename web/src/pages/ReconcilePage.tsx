import { useRef, useState, type DragEvent } from "react";
import { useTranslation } from "react-i18next";
import { useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { useActiveRestaurantId, useReconcileRuns } from "../api/hooks";
import type { ReconcileReport, ReconcileRun } from "../api/types";
import { useToast } from "../components/Toast";
import { Badge, Card, EmptyState } from "../components/ui";
import { formatDateTime } from "../lib/format";

function varianceClass(row: { variance: number; pos_quantity: number }): string {
  const abs = Math.abs(row.variance);
  if (abs === 0) return "var-ok";
  const pct = row.pos_quantity > 0 ? (abs / row.pos_quantity) * 100 : 100;
  if (abs <= 1 || pct <= 5) return "var-ok";
  if (pct <= 15) return "var-warn";
  return "var-bad";
}

export default function ReconcilePage() {
  const { t, i18n } = useTranslation();
  const rid = useActiveRestaurantId();
  const toast = useToast();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [date, setDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [report, setReport] = useState<ReconcileReport | null>(null);
  const { data: runs } = useReconcileRuns(rid);

  const submit = async (selected: File | null = file) => {
    if (!selected || rid === null) return;
    setBusy(true);
    try {
      setReport(await api.reconcile(rid, selected, date || undefined));
      void queryClient.invalidateQueries({ queryKey: ["reconcile-runs", rid] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  /** Load a persisted run back into the report view. */
  const viewRun = (run: ReconcileRun) => {
    setReport({
      restaurant_id: run.restaurant_id,
      default_date: run.date,
      timezone: "",
      rows: run.rows,
      total_pos_quantity: run.total_pos_quantity,
      total_counted_net: run.total_counted_net,
      run_id: run.id,
    });
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragOver(false);
    const dropped = event.dataTransfer.files[0];
    if (dropped) {
      setFile(dropped);
      void submit(dropped);
    }
  };

  return (
    <>
      <Card title={t("reconcile.title")}>
        <p style={{ marginTop: 0, color: "var(--ink-2)", fontSize: 13 }}>{t("reconcile.intro")}</p>
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
            {file ? file.name : t("reconcile.dropHint")}
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              hidden
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>
          <div className="field">
            <label>{t("reconcile.dateLabel")}</label>
            <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="btn-row">
            <button className="btn primary" disabled={!file || busy || rid === null} onClick={() => void submit()}>
              {busy ? t("reconcile.uploading") : t("reconcile.upload")}
            </button>
          </div>
        </div>
      </Card>

      {report === null ? (
        <Card>
          <EmptyState icon="🧮" title={t("reconcile.empty")} hint={t("reconcile.emptyHint")} />
        </Card>
      ) : (
        <Card
          title={
            <>
              <span>
                {t("reconcile.title")} — {report.default_date}
              </span>
              <span className="btn-row">
                <Badge>
                  {t("reconcile.totalPos")}: {report.total_pos_quantity}
                </Badge>
                <Badge>
                  {t("reconcile.totalCounted")}: {report.total_counted_net}
                </Badge>
              </span>
            </>
          }
        >
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("common.category")}</th>
                  <th>{t("common.date")}</th>
                  <th className="num">{t("reconcile.posQty")}</th>
                  <th className="num">{t("common.out")}</th>
                  <th className="num">{t("common.in")}</th>
                  <th className="num">{t("common.net")}</th>
                  <th className="num">{t("reconcile.variance")}</th>
                  <th className="num">{t("reconcile.variancePct")}</th>
                </tr>
              </thead>
              <tbody>
                {report.rows.map((row, index) => (
                  <tr key={`${row.category_key}-${row.date}-${index}`}>
                    <td>
                      <span className="btn-row">
                        {row.category_key}
                        {row.unknown_category && <Badge tone="warn">{t("reconcile.unknownCategory")}</Badge>}
                      </span>
                    </td>
                    <td>{row.date}</td>
                    <td className="num">{row.pos_quantity}</td>
                    <td className="num">{row.counted_out}</td>
                    <td className="num">{row.counted_in}</td>
                    <td className="num">{row.counted_net}</td>
                    <td className={`num ${varianceClass(row)}`}>
                      {row.variance > 0 ? `+${row.variance}` : row.variance}
                    </td>
                    <td className={`num ${varianceClass(row)}`}>
                      {row.variance_pct === null ? "—" : `${row.variance_pct}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      <Card title={t("reconcile.historyTitle")}>
        {runs && runs.items.length === 0 && (
          <EmptyState icon="🗂️" title={t("reconcile.historyEmpty")} hint={t("reconcile.historyEmptyHint")} />
        )}
        {runs && runs.items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("common.time")}</th>
                  <th>{t("common.date")}</th>
                  <th>{t("reconcile.file")}</th>
                  <th className="num">{t("reconcile.totalPos")}</th>
                  <th className="num">{t("reconcile.totalCounted")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {runs.items.map((run) => (
                  <tr key={run.id}>
                    <td style={{ whiteSpace: "nowrap" }}>{formatDateTime(run.created_ts, i18n.language)}</td>
                    <td>{run.date}</td>
                    <td>{run.uploaded_filename ?? "—"}</td>
                    <td className="num">{run.total_pos_quantity}</td>
                    <td className="num">{run.total_counted_net}</td>
                    <td>
                      <button className="btn sm" onClick={() => viewRun(run)}>
                        {t("reconcile.view")}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </>
  );
}
