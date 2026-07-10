import { Navigate, Route, Routes } from "react-router-dom";
import { useTranslation } from "react-i18next";

import AuthPrompt from "./components/AuthPrompt";
import Layout from "./components/Layout";
import { EmptyState } from "./components/ui";
import LivePage from "./pages/LivePage";
import StatsPage from "./pages/StatsPage";
import EventsPage from "./pages/EventsPage";
import ReconcilePage from "./pages/ReconcilePage";
import SystemPage from "./pages/SystemPage";
import RestaurantsPage from "./pages/admin/RestaurantsPage";
import CamerasPage from "./pages/admin/CamerasPage";
import LineEditorPage from "./pages/admin/LineEditorPage";
import ExitGroupsPage from "./pages/admin/ExitGroupsPage";
import MenuPage from "./pages/admin/MenuPage";
import MappingsPage from "./pages/admin/MappingsPage";
import ModelsPage from "./pages/admin/ModelsPage";

function NotFound() {
  const { t } = useTranslation();
  return <EmptyState icon="🧭" title={t("app.notFound")} hint={t("app.notFoundHint")} />;
}

export default function App() {
  return (
    <>
      <AuthPrompt />
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Navigate to="/live" replace />} />
          <Route path="/live" element={<LivePage />} />
          <Route path="/stats" element={<StatsPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/reconcile" element={<ReconcilePage />} />
          <Route path="/system" element={<SystemPage />} />
          <Route path="/admin/restaurants" element={<RestaurantsPage />} />
          <Route path="/admin/cameras" element={<CamerasPage />} />
          <Route path="/admin/cameras/:id/lines" element={<LineEditorPage />} />
          <Route path="/admin/exit-groups" element={<ExitGroupsPage />} />
          <Route path="/admin/menu" element={<MenuPage />} />
          <Route path="/admin/mappings" element={<MappingsPage />} />
          <Route path="/admin/models" element={<ModelsPage />} />
          <Route path="*" element={<NotFound />} />
        </Route>
      </Routes>
    </>
  );
}
