import { useTranslation } from "react-i18next";

import { useActiveRestaurantId, useModelMutations, useModels } from "../../api/hooks";
import { useToast } from "../../components/Toast";
import { Badge, Card, EmptyState, Skeleton } from "../../components/ui";

function classCount(classesJson: string): number {
  try {
    const parsed = JSON.parse(classesJson) as unknown;
    if (Array.isArray(parsed)) return parsed.length;
    if (parsed && typeof parsed === "object") return Object.keys(parsed).length;
  } catch {
    // malformed classes_json: report 0
  }
  return 0;
}

function metricsSummary(metricsJson: string | null): string {
  if (!metricsJson) return "—";
  try {
    const parsed = JSON.parse(metricsJson) as Record<string, unknown>;
    const parts = Object.entries(parsed)
      .filter(([, value]) => typeof value === "number")
      .slice(0, 3)
      .map(([key, value]) => `${key}: ${(value as number).toFixed(3)}`);
    return parts.length > 0 ? parts.join("  ·  ") : "—";
  } catch {
    return "—";
  }
}

export default function ModelsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const rid = useActiveRestaurantId();
  const { data: models, isLoading } = useModels(rid);
  const { activate } = useModelMutations(rid, {
    onError: (e) => toast.error(e.message),
    onSuccess: () => toast.success(t("common.saved")),
  });

  return (
    <Card title={t("admin.models.title")}>
      {isLoading && <Skeleton height={20} count={4} />}
      {models && models.items.length === 0 && (
        <EmptyState icon="🧠" title={t("admin.models.empty")} hint={t("admin.models.emptyHint")} />
      )}
      {models && models.items.length > 0 && (
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th>{t("common.name")}</th>
                <th>{t("admin.models.version")}</th>
                <th>{t("admin.models.kind")}</th>
                <th>{t("admin.models.path")}</th>
                <th>{t("admin.models.metrics")}</th>
                <th>{t("common.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {models.items.map((model) => (
                <tr key={model.id}>
                  <td>
                    <span className="btn-row">
                      {model.name}
                      {model.is_active && <Badge tone="good">{t("admin.models.activeBadge")}</Badge>}
                    </span>
                  </td>
                  <td>{model.version}</td>
                  <td>
                    <Badge>{model.kind}</Badge>
                  </td>
                  <td style={{ maxWidth: 260, overflowWrap: "anywhere", fontSize: 12, color: "var(--ink-2)" }}>
                    {model.path}
                    <div style={{ color: "var(--ink-3)" }}>
                      {t("admin.models.classes", { count: classCount(model.classes_json) })}
                    </div>
                  </td>
                  <td style={{ fontSize: 12 }}>{metricsSummary(model.metrics_json)}</td>
                  <td>
                    <button
                      className="btn sm primary"
                      disabled={model.is_active || activate.isPending}
                      onClick={() => activate.mutate(model.id)}
                    >
                      {t("common.activate")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
