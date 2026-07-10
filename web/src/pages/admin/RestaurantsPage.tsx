import { useState } from "react";
import { useTranslation } from "react-i18next";

import { useRestaurantMutations, useRestaurants, useSystemInfo } from "../../api/hooks";
import type { Restaurant } from "../../api/types";
import { useToast } from "../../components/Toast";
import { Badge, Card, EmptyState, Skeleton } from "../../components/ui";

function RestaurantRow({ restaurant, active }: { restaurant: Restaurant; active: boolean }) {
  const { t } = useTranslation();
  const toast = useToast();
  const { update, remove } = useRestaurantMutations({ onError: (e) => toast.error(e.message) });
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(restaurant.name);
  const [timezone, setTimezone] = useState(restaurant.timezone);
  const [locale, setLocale] = useState(restaurant.locale_default);

  if (!editing) {
    return (
      <tr>
        <td>
          <span className="btn-row">
            {restaurant.name}
            {active && <Badge tone="accent">{t("admin.restaurants.activeBadge")}</Badge>}
          </span>
        </td>
        <td>{restaurant.slug}</td>
        <td>{restaurant.timezone}</td>
        <td>{restaurant.locale_default}</td>
        <td>
          <span className="btn-row">
            <button className="btn sm" onClick={() => setEditing(true)}>
              {t("common.edit")}
            </button>
            <button
              className="btn sm danger"
              disabled={active || remove.isPending}
              onClick={() => {
                if (window.confirm(t("common.confirmDelete"))) remove.mutate(restaurant.id);
              }}
            >
              {t("common.delete")}
            </button>
          </span>
        </td>
      </tr>
    );
  }
  return (
    <tr>
      <td>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </td>
      <td>{restaurant.slug}</td>
      <td>
        <input className="input" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
      </td>
      <td>
        <select className="input" value={locale} onChange={(e) => setLocale(e.target.value)}>
          <option value="de">de</option>
          <option value="en">en</option>
        </select>
      </td>
      <td>
        <span className="btn-row">
          <button
            className="btn sm primary"
            disabled={update.isPending}
            onClick={() =>
              update.mutate(
                { id: restaurant.id, body: { name, timezone, locale_default: locale } },
                { onSuccess: () => setEditing(false) },
              )
            }
          >
            {t("common.save")}
          </button>
          <button className="btn sm" onClick={() => setEditing(false)}>
            {t("common.cancel")}
          </button>
        </span>
      </td>
    </tr>
  );
}

export default function RestaurantsPage() {
  const { t } = useTranslation();
  const toast = useToast();
  const { data, isLoading } = useRestaurants();
  const { data: info } = useSystemInfo();
  const { create } = useRestaurantMutations({
    onError: (e) => toast.error(e.message),
    onSuccess: () => toast.success(t("common.saved")),
  });
  const [slug, setSlug] = useState("");
  const [name, setName] = useState("");
  const [timezone, setTimezone] = useState("Europe/Vienna");

  return (
    <>
      <Card title={t("admin.restaurants.title")}>
        {isLoading && <Skeleton height={20} count={4} />}
        {data && data.items.length === 0 && <EmptyState title={t("common.emptyDefault")} />}
        {data && data.items.length > 0 && (
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>{t("common.name")}</th>
                  <th>{t("admin.restaurants.slug")}</th>
                  <th>{t("admin.restaurants.timezone")}</th>
                  <th>{t("admin.restaurants.locale")}</th>
                  <th>{t("common.actions")}</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((restaurant) => (
                  <RestaurantRow
                    key={restaurant.id}
                    restaurant={restaurant}
                    active={info?.active_restaurant?.id === restaurant.id}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <Card title={t("admin.restaurants.createTitle")}>
        <div className="form-grid">
          <div className="field">
            <label>{t("admin.restaurants.slug")}</label>
            <input value={slug} onChange={(e) => setSlug(e.target.value)} placeholder="trattoria-1" />
          </div>
          <div className="field">
            <label>{t("common.name")}</label>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="field">
            <label>{t("admin.restaurants.timezone")}</label>
            <input value={timezone} onChange={(e) => setTimezone(e.target.value)} />
          </div>
          <button
            className="btn primary"
            disabled={!slug || !name || create.isPending}
            onClick={() =>
              create.mutate(
                { slug, name, timezone },
                {
                  onSuccess: () => {
                    setSlug("");
                    setName("");
                  },
                },
              )
            }
          >
            {t("common.create")}
          </button>
        </div>
      </Card>
    </>
  );
}
