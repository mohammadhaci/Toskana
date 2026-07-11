import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function Card(props: { title?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${props.className ?? ""}`}>
      {props.title !== undefined && <div className="card-title">{props.title}</div>}
      {props.children}
    </section>
  );
}

export function Badge(props: {
  tone?: "neutral" | "good" | "bad" | "warn" | "accent";
  children: ReactNode;
}) {
  const tone = props.tone && props.tone !== "neutral" ? props.tone : "";
  return <span className={`badge ${tone}`}>{props.children}</span>;
}

export function ColorChip({ color }: { color: string | null | undefined }) {
  return <span className="chip" style={{ background: color ?? "#b3b1ab" }} aria-hidden="true" />;
}

export function Skeleton(props: { height?: number; width?: string; count?: number }) {
  const items = Array.from({ length: props.count ?? 1 });
  return (
    <>
      {items.map((_, i) => (
        <div
          key={i}
          className="skeleton"
          style={{ height: props.height ?? 18, width: props.width ?? "100%", marginBottom: 8 }}
        />
      ))}
    </>
  );
}

export function EmptyState(props: { icon?: string; title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-icon" aria-hidden="true">
        {props.icon ?? "🍂"}
      </div>
      <h3>{props.title}</h3>
      {props.hint && <p>{props.hint}</p>}
      {props.children}
    </div>
  );
}

export function LoadError({ retry }: { retry?: () => void }) {
  const { t } = useTranslation();
  return (
    <EmptyState icon="⚠️" title={t("common.error")} hint={t("common.loadError")}>
      {retry && (
        <p style={{ marginTop: 12 }}>
          <button className="btn" onClick={retry}>
            {t("common.retry")}
          </button>
        </p>
      )}
    </EmptyState>
  );
}

export function Pagination(props: {
  total: number;
  limit: number;
  offset: number;
  onOffset: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const pages = Math.max(1, Math.ceil(props.total / props.limit));
  const page = Math.floor(props.offset / props.limit) + 1;
  return (
    <div className="btn-row" style={{ justifyContent: "flex-end", marginTop: 12 }}>
      <span style={{ fontSize: 12, color: "var(--ink-3)" }}>{t("common.page", { page, pages })}</span>
      <button
        className="btn sm"
        disabled={props.offset <= 0}
        onClick={() => props.onOffset(Math.max(0, props.offset - props.limit))}
      >
        {t("common.prev")}
      </button>
      <button
        className="btn sm"
        disabled={props.offset + props.limit >= props.total}
        onClick={() => props.onOffset(props.offset + props.limit)}
      >
        {t("common.next")}
      </button>
    </div>
  );
}
