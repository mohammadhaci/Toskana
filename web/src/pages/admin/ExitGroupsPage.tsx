import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useActiveRestaurantId,
  useCameras,
  useExitGroupMutations,
  useExitGroups,
} from "../../api/hooks";
import type { DedupStrategy, ExitGroup } from "../../api/types";
import { useToast } from "../../components/Toast";
import { Badge, Card, EmptyState, Skeleton } from "../../components/ui";

function GroupRow({ group, rid, cameraNames }: { group: ExitGroup; rid: number; cameraNames: string[] }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { update, remove } = useExitGroupMutations(rid, { onError: (e) => toast.error(e.message) });
  const [form, setForm] = useState({
    name: group.name,
    dedup_window_ms: group.dedup_window_ms,
    dedup_strategy: group.dedup_strategy,
  });

  return (
    <tr>
      <td>
        <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </td>
      <td>
        <input
          className="input"
          type="number"
          min={0}
          max={60000}
          step={100}
          style={{ width: 100 }}
          value={form.dedup_window_ms}
          onChange={(e) => setForm({ ...form, dedup_window_ms: Number(e.target.value) })}
        />
      </td>
      <td>
        <select
          className="input"
          value={form.dedup_strategy}
          onChange={(e) => setForm({ ...form, dedup_strategy: e.target.value as DedupStrategy })}
        >
          <option value="primary_wins">{t("admin.exitGroups.strategies.primary_wins")}</option>
          <option value="first_wins">{t("admin.exitGroups.strategies.first_wins")}</option>
        </select>
      </td>
      <td>
        <span className="btn-row">
          {cameraNames.length === 0 ? (
            <span style={{ color: "var(--ink-3)" }}>{t("common.none")}</span>
          ) : (
            cameraNames.map((name) => <Badge key={name}>{name}</Badge>)
          )}
        </span>
      </td>
      <td>
        <span className="btn-row">
          <button
            className="btn sm primary"
            disabled={update.isPending}
            onClick={() =>
              update.mutate(
                { id: group.id, body: form },
                { onSuccess: () => toast.success(t("common.saved")) },
              )
            }
          >
            {t("common.save")}
          </button>
          <button
            className="btn sm danger"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(t("common.confirmDelete"))) remove.mutate(group.id);
            }}
          >
            {t("common.delete")}
          </button>
        </span>
      </td>
    </tr>
  );
}

export default function ExitGroupsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const rid = useActiveRestaurantId();
  const { data: groups, isLoading } = useExitGroups(rid);
  const { data: cameras } = useCameras(rid);
  const { create } = useExitGroupMutations(rid, { onError: (e) => toast.error(e.message) });
  const [name, setName] = useState("");

  const camerasOf = (groupId: number) =>
    (cameras?.items ?? []).filter((c) => c.exit_group_id === groupId).map((c) => c.name);

  return (
    <>
      <Card title={t("admin.exitGroups.title")}>
        {isLoading && <Skeleton height={20} count={3} />}
        {groups && groups.items.length === 0 && (
          <EmptyState icon="🚪" title={t("admin.exitGroups.empty")} hint={t("admin.exitGroups.emptyHint")} />
        )}
        {groups && groups.items.length > 0 && rid !== null && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("admin.exitGroups.windowMs")}</th>
                  <th>{t("admin.exitGroups.strategy")}</th>
                  <th>{t("admin.exitGroups.camerasInGroup")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {groups.items.map((group) => (
                  <GroupRow key={group.id} group={group} rid={rid} cameraNames={camerasOf(group.id)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={t("admin.exitGroups.createTitle")}>
        <div className="form-grid">
          <div className="field">
            <label>{t("common.name")}</label>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <button
            className="btn primary"
            disabled={!name || create.isPending}
            onClick={() => create.mutate({ name }, { onSuccess: () => setName("") })}
          >
            {t("common.create")}
          </button>
        </div>
      </Card>
    </>
  );
}
