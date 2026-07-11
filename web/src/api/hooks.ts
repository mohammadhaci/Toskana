/** React-query hooks per resource. All restaurant-scoped hooks take the
 * active restaurant id (`rid`) resolved once from `/api/system/info`. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "./client";
import type {
  CameraCreate,
  CameraUpdate,
  CategoryCreate,
  CategoryUpdate,
  ExitGroupCreate,
  ExitGroupUpdate,
  LineCreate,
  LineUpdate,
  MappingCreate,
  MappingUpdate,
  MenuItemCreate,
  MenuItemUpdate,
  RestaurantCreate,
  RestaurantUpdate,
} from "./types";

export function useSystemInfo() {
  return useQuery({ queryKey: ["system", "info"], queryFn: api.systemInfo, staleTime: 60_000 });
}

/** The active restaurant id all dashboard URLs derive from (null while loading). */
export function useActiveRestaurantId(): number | null {
  const { data } = useSystemInfo();
  return data?.active_restaurant?.id ?? null;
}

export function useSystemHealth(intervalMs = 5000) {
  return useQuery({
    queryKey: ["system", "health"],
    queryFn: api.systemHealth,
    refetchInterval: intervalMs,
  });
}

/** Dashboard-managed AI Event Refiner settings (masked; no api_key). */
export function useRefinerSettings() {
  return useQuery({ queryKey: ["settings", "refiner"], queryFn: api.getRefinerSettings });
}

// -- reads -------------------------------------------------------------------

export function useRestaurants() {
  return useQuery({ queryKey: ["restaurants"], queryFn: api.listRestaurants });
}

export function useCameras(rid: number | null) {
  return useQuery({
    queryKey: ["cameras", rid],
    queryFn: () => api.listCameras(rid as number),
    enabled: rid !== null,
  });
}

/** Static vendor preset list for the camera wizard. */
export function useCameraPresets() {
  return useQuery({
    queryKey: ["camera-presets"],
    queryFn: api.listCameraPresets,
    staleTime: Infinity,
  });
}

export function useCameraStatus(cameraId: number, intervalMs = 5000) {
  return useQuery({
    queryKey: ["camera-status", cameraId],
    queryFn: () => api.cameraStatus(cameraId),
    refetchInterval: intervalMs,
  });
}

export function useLines(cameraId: number | null) {
  return useQuery({
    queryKey: ["lines", cameraId],
    queryFn: () => api.listLines(cameraId as number),
    enabled: cameraId !== null,
  });
}

export function useExitGroups(rid: number | null) {
  return useQuery({
    queryKey: ["exit-groups", rid],
    queryFn: () => api.listExitGroups(rid as number),
    enabled: rid !== null,
  });
}

export function useCategories(rid: number | null) {
  return useQuery({
    queryKey: ["categories", rid],
    queryFn: () => api.listCategories(rid as number),
    enabled: rid !== null,
  });
}

export function useMenuItems(rid: number | null) {
  return useQuery({
    queryKey: ["menu-items", rid],
    queryFn: () => api.listMenuItems(rid as number),
    enabled: rid !== null,
  });
}

export function useMappings(rid: number | null, modelId?: number) {
  return useQuery({
    queryKey: ["mappings", rid, modelId ?? "all"],
    queryFn: () => api.listMappings(rid as number, modelId),
    enabled: rid !== null,
  });
}

export function useModels(rid: number | null) {
  return useQuery({
    queryKey: ["models", rid],
    queryFn: () => api.listModels(rid as number),
    enabled: rid !== null,
  });
}

export function useEvents(rid: number | null, query: string) {
  return useQuery({
    queryKey: ["events", rid, query],
    queryFn: () => api.listEvents(rid as number, query),
    enabled: rid !== null,
    placeholderData: (previous) => previous,
  });
}

export function useTimeseries(
  rid: number | null,
  params: { bucket: "hour" | "day"; from_ts?: number; to_ts?: number; group_by?: string },
) {
  return useQuery({
    queryKey: ["timeseries", rid, params],
    queryFn: () => api.timeseries(rid as number, params),
    enabled: rid !== null,
  });
}

export function useStatsSummary(rid: number | null) {
  return useQuery({
    queryKey: ["stats-summary", rid],
    queryFn: () => api.statsSummary(rid as number),
    enabled: rid !== null,
  });
}

export function useDataGaps(
  rid: number | null,
  params: { from_ts?: number; to_ts?: number; camera_id?: number },
) {
  return useQuery({
    queryKey: ["data-gaps", rid, params],
    queryFn: () => api.listDataGaps(rid as number, params),
    enabled: rid !== null,
  });
}

export function useReconcileRuns(rid: number | null) {
  return useQuery({
    queryKey: ["reconcile-runs", rid],
    queryFn: () => api.listReconcileRuns(rid as number),
    enabled: rid !== null,
  });
}

/** Set of analysis states that still change — drives the polling interval. */
const ANALYSIS_ACTIVE = new Set(["queued", "downloading", "running"]);

/** Analysis jobs, polled every ~2s while at least one job is still active. */
export function useAnalysisJobs(rid: number | null, intervalMs = 2000) {
  return useQuery({
    queryKey: ["analysis", rid],
    queryFn: () => api.listAnalysisJobs(rid as number),
    enabled: rid !== null,
    refetchInterval: (query) =>
      query.state.data?.some((job) => ANALYSIS_ACTIVE.has(job.status)) ? intervalMs : false,
  });
}

// -- mutations (invalidate the matching list key) ------------------------------

type Err = { onSuccess?: () => void; onError?: (error: Error) => void };

function useInvalidatingMutation<TArgs, TResult>(
  keys: unknown[][],
  fn: (args: TArgs) => Promise<TResult>,
  options?: Err,
) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      for (const key of keys) void client.invalidateQueries({ queryKey: key });
      options?.onSuccess?.();
    },
    onError: (error: Error) => options?.onError?.(error),
  });
}

export function useRestaurantMutations(opts?: Err) {
  const keys = [["restaurants"], ["system", "info"]];
  return {
    create: useInvalidatingMutation(keys, (body: RestaurantCreate) => api.createRestaurant(body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: RestaurantUpdate }) => api.updateRestaurant(id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteRestaurant(id), opts),
  };
}

export function useCameraMutations(rid: number | null, opts?: Err) {
  const keys = [["cameras", rid], ["system", "health"]];
  return {
    create: useInvalidatingMutation(keys, (body: CameraCreate) => api.createCamera(rid as number, body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: CameraUpdate }) => api.updateCamera(rid as number, id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteCamera(rid as number, id), opts),
    start: useInvalidatingMutation(
      [...keys, ["camera-status"]],
      (id: number) => api.startCamera(id),
      opts,
    ),
    stop: useInvalidatingMutation(
      [...keys, ["camera-status"]],
      (id: number) => api.stopCamera(id),
      opts,
    ),
    restart: useInvalidatingMutation(
      [...keys, ["camera-status"]],
      (id: number) => api.restartCamera(id),
      opts,
    ),
  };
}

export function useLineMutations(cameraId: number | null, opts?: Err) {
  const keys = [["lines", cameraId]];
  return {
    create: useInvalidatingMutation(keys, (body: LineCreate) => api.createLine(cameraId as number, body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: LineUpdate }) => api.updateLine(cameraId as number, id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteLine(cameraId as number, id), opts),
  };
}

export function useExitGroupMutations(rid: number | null, opts?: Err) {
  const keys = [["exit-groups", rid]];
  return {
    create: useInvalidatingMutation(keys, (body: ExitGroupCreate) => api.createExitGroup(rid as number, body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: ExitGroupUpdate }) => api.updateExitGroup(rid as number, id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteExitGroup(rid as number, id), opts),
  };
}

export function useCategoryMutations(rid: number | null, opts?: Err) {
  const keys = [["categories", rid]];
  return {
    create: useInvalidatingMutation(keys, (body: CategoryCreate) => api.createCategory(rid as number, body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: CategoryUpdate }) => api.updateCategory(rid as number, id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteCategory(rid as number, id), opts),
  };
}

export function useMenuItemMutations(rid: number | null, opts?: Err) {
  const keys = [["menu-items", rid]];
  return {
    create: useInvalidatingMutation(keys, (body: MenuItemCreate) => api.createMenuItem(rid as number, body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: MenuItemUpdate }) => api.updateMenuItem(rid as number, id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteMenuItem(rid as number, id), opts),
  };
}

export function useMappingMutations(rid: number | null, opts?: Err) {
  const keys = [["mappings", rid]];
  return {
    create: useInvalidatingMutation(keys, (body: MappingCreate) => api.createMapping(rid as number, body), opts),
    update: useInvalidatingMutation(
      keys,
      ({ id, body }: { id: number; body: MappingUpdate }) => api.updateMapping(rid as number, id, body),
      opts,
    ),
    remove: useInvalidatingMutation(keys, (id: number) => api.deleteMapping(rid as number, id), opts),
  };
}

export function useModelMutations(rid: number | null, opts?: Err) {
  const keys = [["models", rid], ["mappings", rid]];
  return {
    activate: useInvalidatingMutation(keys, (id: number) => api.activateModel(rid as number, id), opts),
  };
}

export function useEventMutations(rid: number | null, opts?: Err) {
  const keys = [["events", rid]];
  return {
    setCanonical: useInvalidatingMutation(
      keys,
      ({ id, isCanonical }: { id: string; isCanonical: boolean }) =>
        api.patchEvent(rid as number, id, isCanonical),
      opts,
    ),
  };
}
