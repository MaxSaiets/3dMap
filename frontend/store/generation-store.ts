import { create } from "zustand";
import { LatLngBounds } from "leaflet";

import type { TaskStatus } from "@/lib/api";

interface GenerationState {
  selectedArea: LatLngBounds | null;
  isGenerating: boolean;
  // Для single: taskGroupId === activeTaskId
  // Для batch: taskGroupId === "batch_<uuid>", activeTaskId === один з taskIds
  taskGroupId: string | null;
  taskIds: string[];
  activeTaskId: string | null;
  progress: number;
  status: string;
  downloadUrl: string | null;
  taskStatuses: Record<string, TaskStatus>;
  showAllZones: boolean;
  
  // Параметри генерації
  roadWidthMultiplier: number;
  roadHeightMm: number;
  roadEmbedMm: number;
  buildingMinHeight: number;
  buildingHeightMultiplier: number;
  buildingFoundationMm: number;
  buildingEmbedMm: number;
  waterDepth: number;
  terrainEnabled: boolean;
  terrainZScale: number;
  terrainBaseThicknessMm: number;
  terrainResolution: number;
  terrariumZoom: number;
  exportFormat: "stl" | "3mf";
  modelSizeMm: number; // Розмір моделі в міліметрах
  
  // Actions
  setSelectedArea: (area: LatLngBounds | null) => void;
  setGenerating: (isGenerating: boolean) => void;
  setTaskGroup: (groupId: string | null, taskIds?: string[]) => void;
  setActiveTaskId: (taskId: string | null) => void;
  setTaskStatuses: (statuses: Record<string, TaskStatus>) => void;
  setShowAllZones: (value: boolean) => void;
  updateProgress: (progress: number, status: string) => void;
  setDownloadUrl: (url: string | null) => void;
  
  // Параметри
  setRoadWidthMultiplier: (value: number) => void;
  setRoadHeightMm: (value: number) => void;
  setRoadEmbedMm: (value: number) => void;
  setBuildingMinHeight: (value: number) => void;
  setBuildingHeightMultiplier: (value: number) => void;
  setBuildingFoundationMm: (value: number) => void;
  setBuildingEmbedMm: (value: number) => void;
  setWaterDepth: (value: number) => void;
  setTerrainEnabled: (value: boolean) => void;
  setTerrainZScale: (value: number) => void;
  setTerrainBaseThicknessMm: (value: number) => void;
  setTerrainResolution: (value: number) => void;
  setTerrariumZoom: (value: number) => void;
  setExportFormat: (format: "stl" | "3mf") => void;
  setModelSizeMm: (value: number) => void;
  
  reset: () => void;
}

const initialState = {
  selectedArea: null,
  isGenerating: false,
  taskGroupId: null,
  taskIds: [] as string[],
  activeTaskId: null,
  progress: 0,
  status: "",
  downloadUrl: null,
  taskStatuses: {} as Record<string, TaskStatus>,
  showAllZones: false,
  // На 10×10см “реальні” ширини доріг часто виглядають надто товстими — ставимо мʼякший дефолт.
  roadWidthMultiplier: 0.8,
  // Дороги: менша висота + трохи більше втиснення дають кращий вигляд і менше z-fighting
  roadHeightMm: 0.5,
  roadEmbedMm: 0.3,
  // Реальні OSM висоти на масштабі 10x10см часто виглядають занадто низько,
  // тому робимо трохи вищі дефолти (користувач може змінити слайдерами).
  buildingMinHeight: 5.0,
  buildingHeightMultiplier: 1.8,
  buildingFoundationMm: 0.6,
  buildingEmbedMm: 0.2,
  waterDepth: 2.0,
  terrainEnabled: true,
  terrainZScale: 0.5,
  // Тонка “підложка” під рельєф (мм на фінальній моделі)
  terrainBaseThicknessMm: 2.0,
  // Вища деталізація рельєфу -> менші трикутники, більше “реальності”
  terrainResolution: 180,
  terrariumZoom: 15,
  exportFormat: "3mf" as const,
  modelSizeMm: 100.0, // 100мм = 10см за замовчуванням
};

export const useGenerationStore = create<GenerationState>((set) => ({
  ...initialState,
  
  setSelectedArea: (area) => set({ selectedArea: area }),
  setGenerating: (isGenerating) => set({ isGenerating }),
  setTaskGroup: (taskGroupId, taskIds) =>
    set((s) => {
      const nextTaskIds = taskIds ?? (taskGroupId ? [taskGroupId] : []);
      const nextActive = s.activeTaskId && nextTaskIds.includes(s.activeTaskId)
        ? s.activeTaskId
        : (nextTaskIds[0] ?? null);
      return {
        taskGroupId,
        taskIds: nextTaskIds,
        activeTaskId: nextActive,
        // при новій задачі скидаємо статуси
        taskStatuses: {},
      };
    }),
  setActiveTaskId: (activeTaskId) => set({ activeTaskId }),
  setTaskStatuses: (taskStatuses) => set({ taskStatuses }),
  setShowAllZones: (showAllZones) => set({ showAllZones }),
  updateProgress: (progress, status) => set({ progress, status }),
  setDownloadUrl: (url) => set({ downloadUrl: url }),
  
  setRoadWidthMultiplier: (value) => set({ roadWidthMultiplier: value }),
  setRoadHeightMm: (value) => set({ roadHeightMm: value }),
  setRoadEmbedMm: (value) => set({ roadEmbedMm: value }),
  setBuildingMinHeight: (value) => set({ buildingMinHeight: value }),
  setBuildingHeightMultiplier: (value) => set({ buildingHeightMultiplier: value }),
  setBuildingFoundationMm: (value) => set({ buildingFoundationMm: value }),
  setBuildingEmbedMm: (value) => set({ buildingEmbedMm: value }),
  setWaterDepth: (value) => set({ waterDepth: value }),
  setTerrainEnabled: (value) => set({ terrainEnabled: value }),
  setTerrainZScale: (value) => set({ terrainZScale: value }),
  setTerrainBaseThicknessMm: (value) => set({ terrainBaseThicknessMm: value }),
  setTerrainResolution: (value) => set({ terrainResolution: value }),
  setTerrariumZoom: (value) => set({ terrariumZoom: value }),
  setExportFormat: (format) => set({ exportFormat: format }),
  setModelSizeMm: (value) => set({ modelSizeMm: value }),
  
  reset: () => set(initialState),
}));

