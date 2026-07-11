import { useState } from "react";
import { useTranslation } from "react-i18next";

import {
  useActiveRestaurantId,
  useCategories,
  useCategoryMutations,
  useMenuItemMutations,
  useMenuItems,
} from "../../api/hooks";
import type { Category, MenuItem } from "../../api/types";
import { useToast } from "../../components/Toast";
import { Card, ColorChip, EmptyState, Skeleton } from "../../components/ui";
import { categoryLabel } from "../../lib/format";

function CategoryRow({ category, rid }: { category: Category; rid: number }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { update, remove } = useCategoryMutations(rid, { onError: (e) => toast.error(e.message) });
  const [form, setForm] = useState({
    name_de: category.name_de,
    name_en: category.name_en,
    color_hex: category.color_hex.slice(0, 7),
    sort_order: category.sort_order,
  });

  return (
    <tr>
      <td>
        <span className="btn-row">
          <ColorChip color={form.color_hex} />
          {category.key}
        </span>
      </td>
      <td>
        <input className="input" value={form.name_de} onChange={(e) => setForm({ ...form, name_de: e.target.value })} />
      </td>
      <td>
        <input className="input" value={form.name_en} onChange={(e) => setForm({ ...form, name_en: e.target.value })} />
      </td>
      <td>
        <input
          className="input"
          type="color"
          value={form.color_hex}
          onChange={(e) => setForm({ ...form, color_hex: e.target.value })}
          style={{ width: 48, height: 30, padding: 2 }}
        />
      </td>
      <td>
        <input
          className="input"
          type="number"
          style={{ width: 70 }}
          value={form.sort_order}
          onChange={(e) => setForm({ ...form, sort_order: Number(e.target.value) })}
        />
      </td>
      <td>
        <span className="btn-row">
          <button
            className="btn sm primary"
            disabled={update.isPending}
            onClick={() =>
              update.mutate(
                { id: category.id, body: form },
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
              if (window.confirm(t("common.confirmDelete"))) remove.mutate(category.id);
            }}
          >
            {t("common.delete")}
          </button>
        </span>
      </td>
    </tr>
  );
}

function MenuItemRow({
  item,
  categories,
  rid,
}: {
  item: MenuItem;
  categories: Category[];
  rid: number;
}) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const { update, remove } = useMenuItemMutations(rid, { onError: (e) => toast.error(e.message) });
  const [form, setForm] = useState({
    name: item.name,
    category_id: item.category_id,
    price: item.price,
    is_active: item.is_active,
  });

  return (
    <tr>
      <td>
        <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </td>
      <td>
        <select
          className="input"
          value={form.category_id}
          onChange={(e) => setForm({ ...form, category_id: Number(e.target.value) })}
        >
          {categories.map((category) => (
            <option key={category.id} value={category.id}>
              {categoryLabel(category, i18n.language)}
            </option>
          ))}
        </select>
      </td>
      <td>
        <input
          className="input"
          type="number"
          min={0}
          step={0.1}
          style={{ width: 90 }}
          value={form.price ?? ""}
          onChange={(e) => setForm({ ...form, price: e.target.value === "" ? null : Number(e.target.value) })}
        />
      </td>
      <td>
        <input
          type="checkbox"
          checked={form.is_active}
          onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
        />
      </td>
      <td>
        <span className="btn-row">
          <button
            className="btn sm primary"
            disabled={update.isPending}
            onClick={() =>
              update.mutate({ id: item.id, body: form }, { onSuccess: () => toast.success(t("common.saved")) })
            }
          >
            {t("common.save")}
          </button>
          <button
            className="btn sm danger"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(t("common.confirmDelete"))) remove.mutate(item.id);
            }}
          >
            {t("common.delete")}
          </button>
        </span>
      </td>
    </tr>
  );
}

export default function MenuPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const rid = useActiveRestaurantId();
  const { data: categories, isLoading: categoriesLoading } = useCategories(rid);
  const { data: items, isLoading: itemsLoading } = useMenuItems(rid);
  const categoryMutations = useCategoryMutations(rid, { onError: (e) => toast.error(e.message) });
  const itemMutations = useMenuItemMutations(rid, { onError: (e) => toast.error(e.message) });

  const [newCategory, setNewCategory] = useState({ key: "", name_de: "", name_en: "", color_hex: "#c05a2e" });
  const [newItem, setNewItem] = useState({ name: "", category_id: 0, price: "" });

  return (
    <>
      <Card title={t("admin.menu.categoriesTitle")}>
        {categoriesLoading && <Skeleton height={20} count={4} />}
        {categories && categories.items.length === 0 && (
          <EmptyState icon="🏷️" title={t("admin.menu.noCategories")} hint={t("admin.menu.noCategoriesHint")} />
        )}
        {categories && categories.items.length > 0 && rid !== null && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("admin.menu.key")}</th>
                  <th>{t("admin.menu.nameDe")}</th>
                  <th>{t("admin.menu.nameEn")}</th>
                  <th>{t("admin.menu.color")}</th>
                  <th>{t("admin.menu.sortOrder")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {categories.items.map((category) => (
                  <CategoryRow key={category.id} category={category} rid={rid} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="form-grid" style={{ marginTop: 16 }}>
          <div className="field">
            <label>{t("admin.menu.key")}</label>
            <input
              value={newCategory.key}
              onChange={(e) => setNewCategory({ ...newCategory, key: e.target.value })}
              placeholder="pizza"
            />
          </div>
          <div className="field">
            <label>{t("admin.menu.nameDe")}</label>
            <input
              value={newCategory.name_de}
              onChange={(e) => setNewCategory({ ...newCategory, name_de: e.target.value })}
            />
          </div>
          <div className="field">
            <label>{t("admin.menu.nameEn")}</label>
            <input
              value={newCategory.name_en}
              onChange={(e) => setNewCategory({ ...newCategory, name_en: e.target.value })}
            />
          </div>
          <div className="field">
            <label>{t("admin.menu.color")}</label>
            <input
              type="color"
              value={newCategory.color_hex}
              onChange={(e) => setNewCategory({ ...newCategory, color_hex: e.target.value })}
            />
          </div>
          <button
            className="btn primary"
            disabled={!newCategory.key || !newCategory.name_de || !newCategory.name_en || categoryMutations.create.isPending}
            onClick={() =>
              categoryMutations.create.mutate(newCategory, {
                onSuccess: () => setNewCategory({ key: "", name_de: "", name_en: "", color_hex: "#c05a2e" }),
              })
            }
          >
            {t("admin.menu.createCategory")}
          </button>
        </div>
      </Card>

      <Card title={t("admin.menu.itemsTitle")}>
        {itemsLoading && <Skeleton height={20} count={3} />}
        {items && items.items.length === 0 && (
          <EmptyState icon="🍕" title={t("admin.menu.noItems")} hint={t("admin.menu.noItemsHint")} />
        )}
        {items && items.items.length > 0 && rid !== null && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("admin.menu.categoryOf")}</th>
                  <th>{t("admin.menu.price")}</th>
                  <th>{t("common.active")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {items.items.map((item) => (
                  <MenuItemRow key={item.id} item={item} categories={categories?.items ?? []} rid={rid} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {categories && categories.items.length > 0 && (
          <div className="form-grid" style={{ marginTop: 16 }}>
            <div className="field">
              <label>{t("common.name")}</label>
              <input value={newItem.name} onChange={(e) => setNewItem({ ...newItem, name: e.target.value })} />
            </div>
            <div className="field">
              <label>{t("admin.menu.categoryOf")}</label>
              <select
                value={newItem.category_id || categories.items[0]?.id || 0}
                onChange={(e) => setNewItem({ ...newItem, category_id: Number(e.target.value) })}
              >
                {categories.items.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.key}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label>{t("admin.menu.price")}</label>
              <input
                type="number"
                min={0}
                step={0.1}
                value={newItem.price}
                onChange={(e) => setNewItem({ ...newItem, price: e.target.value })}
              />
            </div>
            <button
              className="btn primary"
              disabled={!newItem.name || itemMutations.create.isPending}
              onClick={() =>
                itemMutations.create.mutate(
                  {
                    name: newItem.name,
                    category_id: newItem.category_id || categories.items[0].id,
                    price: newItem.price === "" ? null : Number(newItem.price),
                  },
                  { onSuccess: () => setNewItem({ name: "", category_id: 0, price: "" }) },
                )
              }
            >
              {t("admin.menu.createItem")}
            </button>
          </div>
        )}
      </Card>

    </>
  );
}
