import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useActiveRestaurantId,
  useCategories,
  useMappingMutations,
  useMappings,
  useMenuItems,
  useModels,
} from "../../api/hooks";
import type { Category, Mapping, MenuItem } from "../../api/types";
import { useToast } from "../../components/Toast";
import { Card, ColorChip, EmptyState, Skeleton } from "../../components/ui";
import { categoryLabel } from "../../lib/format";

type TargetKind = "category" | "menu_item";

function targetValue(kind: TargetKind, id: number | null): string {
  return id === null ? "" : `${kind}:${id}`;
}

function TargetSelect({
  categories,
  menuItems,
  value,
  onChange,
}: {
  categories: Category[];
  menuItems: MenuItem[];
  value: string;
  onChange: (value: string) => void;
}) {
  const { t, i18n } = useTranslation();
  return (
    <select className="input" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{t("common.none")}</option>
      <optgroup label={t("admin.mappings.targetCategory")}>
        {categories.map((category) => (
          <option key={category.id} value={`category:${category.id}`}>
            {categoryLabel(category, i18n.language)}
          </option>
        ))}
      </optgroup>
      <optgroup label={t("admin.mappings.targetMenuItem")}>
        {menuItems.map((item) => (
          <option key={item.id} value={`menu_item:${item.id}`}>
            {item.name}
          </option>
        ))}
      </optgroup>
    </select>
  );
}

function parseTarget(value: string): { category_id: number | null; menu_item_id: number | null } {
  if (!value) return { category_id: null, menu_item_id: null };
  const [kind, id] = value.split(":");
  return kind === "category"
    ? { category_id: Number(id), menu_item_id: null }
    : { category_id: null, menu_item_id: Number(id) };
}

function MappingRow({
  mapping,
  categories,
  menuItems,
  rid,
}: {
  mapping: Mapping;
  categories: Category[];
  menuItems: MenuItem[];
  rid: number;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const { update, remove } = useMappingMutations(rid, { onError: (e) => toast.error(e.message) });
  const [target, setTarget] = useState(
    mapping.category_id !== null
      ? targetValue("category", mapping.category_id)
      : targetValue("menu_item", mapping.menu_item_id),
  );
  const [minConfidence, setMinConfidence] = useState(mapping.min_confidence);

  const category = categories.find((c) => c.id === mapping.category_id) ?? null;

  return (
    <tr>
      <td className="num">{mapping.model_class_id}</td>
      <td>
        <span className="btn-row">
          {category && <ColorChip color={category.color_hex} />}
          {mapping.model_class_name}
        </span>
      </td>
      <td>
        <TargetSelect categories={categories} menuItems={menuItems} value={target} onChange={setTarget} />
      </td>
      <td>
        <input
          className="input"
          type="number"
          min={0}
          max={1}
          step={0.05}
          style={{ width: 80 }}
          value={minConfidence}
          onChange={(e) => setMinConfidence(Number(e.target.value))}
        />
      </td>
      <td>
        <span className="btn-row">
          <button
            className="btn sm primary"
            disabled={update.isPending || !target}
            onClick={() =>
              update.mutate(
                { id: mapping.id, body: { ...parseTarget(target), min_confidence: minConfidence } },
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
              if (window.confirm(t("common.confirmDelete"))) remove.mutate(mapping.id);
            }}
          >
            {t("common.delete")}
          </button>
        </span>
      </td>
    </tr>
  );
}

export default function MappingsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const rid = useActiveRestaurantId();
  const { data: models } = useModels(rid);
  const [modelId, setModelId] = useState<number | null>(null);

  useEffect(() => {
    if (modelId === null && models && models.items.length > 0) {
      const active = models.items.find((m) => m.is_active) ?? models.items[0];
      setModelId(active.id);
    }
  }, [models, modelId]);

  const { data: mappings, isLoading } = useMappings(rid, modelId ?? undefined);
  const { data: categories } = useCategories(rid);
  const { data: menuItems } = useMenuItems(rid);
  const { create } = useMappingMutations(rid, { onError: (e) => toast.error(e.message) });

  const [newMapping, setNewMapping] = useState({ model_class_id: "", model_class_name: "", target: "", min_confidence: 0.35 });

  return (
    <>
      <Card
        title={
          <>
            <span>{t("admin.mappings.title")}</span>
            <span className="btn-row">
              <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{t("admin.mappings.model")}</span>
              <select
                className="input"
                value={modelId ?? ""}
                onChange={(e) => setModelId(e.target.value ? Number(e.target.value) : null)}
              >
                {models?.items.map((model) => (
                  <option key={model.id} value={model.id}>
                    {model.name} {model.version}
                    {model.is_active ? ` — ${t("admin.models.activeBadge")}` : ""}
                  </option>
                ))}
              </select>
            </span>
          </>
        }
      >
        {isLoading && <Skeleton height={20} count={5} />}
        {mappings && mappings.items.length === 0 && (
          <EmptyState icon="🔗" title={t("admin.mappings.empty")} hint={t("admin.mappings.emptyHint")} />
        )}
        {mappings && mappings.items.length > 0 && rid !== null && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th className="num">{t("admin.mappings.classId")}</th>
                  <th>{t("admin.mappings.className")}</th>
                  <th>{t("admin.mappings.target")}</th>
                  <th>{t("admin.mappings.minConfidence")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {mappings.items.map((mapping) => (
                  <MappingRow
                    key={mapping.id}
                    mapping={mapping}
                    categories={categories?.items ?? []}
                    menuItems={menuItems?.items ?? []}
                    rid={rid}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {modelId !== null && (
        <Card title={t("admin.mappings.createTitle")}>
          <div className="form-grid">
            <div className="field">
              <label>{t("admin.mappings.classId")}</label>
              <input
                type="number"
                min={0}
                value={newMapping.model_class_id}
                onChange={(e) => setNewMapping({ ...newMapping, model_class_id: e.target.value })}
              />
            </div>
            <div className="field">
              <label>{t("admin.mappings.className")}</label>
              <input
                value={newMapping.model_class_name}
                onChange={(e) => setNewMapping({ ...newMapping, model_class_name: e.target.value })}
                placeholder="cup"
              />
            </div>
            <div className="field">
              <label>{t("admin.mappings.target")}</label>
              <TargetSelect
                categories={categories?.items ?? []}
                menuItems={menuItems?.items ?? []}
                value={newMapping.target}
                onChange={(target) => setNewMapping({ ...newMapping, target })}
              />
            </div>
            <div className="field">
              <label>{t("admin.mappings.minConfidence")}</label>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={newMapping.min_confidence}
                onChange={(e) => setNewMapping({ ...newMapping, min_confidence: Number(e.target.value) })}
              />
            </div>
            <button
              className="btn primary"
              disabled={
                newMapping.model_class_id === "" ||
                !newMapping.model_class_name ||
                !newMapping.target ||
                create.isPending
              }
              onClick={() =>
                create.mutate(
                  {
                    model_id: modelId,
                    model_class_id: Number(newMapping.model_class_id),
                    model_class_name: newMapping.model_class_name,
                    ...parseTarget(newMapping.target),
                    min_confidence: newMapping.min_confidence,
                  },
                  {
                    onSuccess: () => {
                      setNewMapping({ model_class_id: "", model_class_name: "", target: "", min_confidence: 0.35 });
                      toast.success(t("common.saved"));
                    },
                  },
                )
              }
            >
              {t("common.create")}
            </button>
          </div>
        </Card>
      )}
    </>
  );
}
