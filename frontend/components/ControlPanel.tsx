"use client";

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { useGenerationStore } from "@/store/generation-store";
import { api } from "@/lib/api";
import { Download, Loader2, Play, Grid } from "lucide-react";

// Динамічний імпорт HexagonalGrid з вимкненим SSR (Leaflet потребує window)
const HexagonalGrid = dynamic(() => import("./HexagonalGrid"), {
  ssr: false,
  loading: () => <div className="flex items-center justify-center h-full">Завантаження карти...</div>
});

interface ControlPanelProps {
  showHexGrid?: boolean;
  setShowHexGrid?: (show: boolean) => void;
  selectedZones?: any[];
  setSelectedZones?: (zones: any[]) => void;
}

export function ControlPanel({ 
  showHexGrid: externalShowHexGrid, 
  setShowHexGrid: externalSetShowHexGrid,
  selectedZones: externalSelectedZones,
  setSelectedZones: externalSetSelectedZones,
}: ControlPanelProps = {}) {
  const {
    selectedArea,
    isGenerating,
    taskGroupId,
    taskIds,
    activeTaskId,
    taskStatuses,
    showAllZones,
    progress,
    status,
    downloadUrl,
    roadWidthMultiplier,
    roadHeightMm,
    roadEmbedMm,
    buildingMinHeight,
    buildingHeightMultiplier,
    buildingFoundationMm,
    buildingEmbedMm,
    waterDepth,
    terrainEnabled,
    terrainZScale,
    terrainBaseThicknessMm,
    terrainResolution,
    terrariumZoom,
    exportFormat,
    modelSizeMm,
    setRoadWidthMultiplier,
    setRoadHeightMm,
    setRoadEmbedMm,
    setBuildingMinHeight,
    setBuildingHeightMultiplier,
    setBuildingFoundationMm,
    setBuildingEmbedMm,
    setWaterDepth,
    setTerrainEnabled,
    setTerrainZScale,
    setTerrainBaseThicknessMm,
    setTerrainResolution,
    setTerrariumZoom,
    setExportFormat,
    setModelSizeMm,
    setGenerating,
    setTaskGroup,
    setActiveTaskId,
    setTaskStatuses,
    setShowAllZones,
    updateProgress,
    setDownloadUrl,
  } = useGenerationStore();

  const [error, setError] = useState<string | null>(null);
  // Використовуємо зовнішні стани якщо передані, інакше внутрішні
  const [internalShowHexGrid, setInternalShowHexGrid] = useState(false);
  const [internalSelectedZones, setInternalSelectedZones] = useState<any[]>([]);
  
  const showHexGrid = externalShowHexGrid !== undefined ? externalShowHexGrid : internalShowHexGrid;
  const setShowHexGrid = externalSetShowHexGrid || setInternalShowHexGrid;
  const selectedZones = externalSelectedZones !== undefined ? externalSelectedZones : internalSelectedZones;
  const setSelectedZones = externalSetSelectedZones || setInternalSelectedZones;

  // Перевірка статусу задачі
  useEffect(() => {
    if (!taskGroupId || !isGenerating) return;

    const interval = setInterval(async () => {
      try {
        // Якщо є багато taskIds -> опитуємо КОЖЕН task_id напряму.
        // Це надійніше, ніж покладатися на batch endpoint (який може зламатися при dev-reload).
        if (taskIds && taskIds.length > 1) {
          const results = await Promise.all(
            taskIds.map(async (id) => {
              try {
                return await api.getStatus(id);
              } catch (e) {
                return { task_id: id, status: "failed", progress: 0, message: "Status fetch failed", download_url: null } as any;
              }
            })
          );

          const tasksList = results as any[];
          const total = tasksList.length;
          const completed = tasksList.filter((t) => t.status === "completed").length;
          const failed = tasksList.filter((t) => t.status === "failed").length;

          const map: Record<string, any> = {};
          for (const t of tasksList) map[t.task_id] = t;
          setTaskStatuses(map);

          const avg = tasksList.length
            ? Math.round(tasksList.reduce((s, t) => s + (t.progress || 0), 0) / tasksList.length)
            : 0;
          updateProgress(avg, `Зони: ${completed}/${total} готово${failed ? `, помилок: ${failed}` : ""}`);

          // даємо downloadUrl для активної, якщо вона готова (але НЕ зупиняємо batch)
          const active = (activeTaskId ? map[activeTaskId] : null) || (taskIds[0] ? map[taskIds[0]] : null);
          if (active && active.status === "completed") {
            setDownloadUrl(active.download_url);
          }

          // завершуємо генерацію тільки коли всі або completed, або failed
          if (completed + failed >= total) {
            setGenerating(false);
            if (failed) {
              const firstFailed = tasksList.find((t) => t.status === "failed");
              if (firstFailed) setError(firstFailed.message || "Одна з зон не згенерувалась");
            }
          }
          return;
        }

        // single mode (або поки taskIds ще не виставлено)
        const resp = await api.getStatus(taskGroupId);
        const single = resp as any;
        updateProgress(single.progress, single.message);
        if (single.status === "completed") {
          setGenerating(false);
          setDownloadUrl(single.download_url);
        } else if (single.status === "failed") {
          setGenerating(false);
          setError(single.message);
        }
      } catch (err) {
        console.error("Помилка перевірки статусу:", err);
      }
    }, 2000); // Перевірка кожні 2 секунди

    return () => clearInterval(interval);
  }, [taskGroupId, isGenerating, updateProgress, setGenerating, setDownloadUrl, activeTaskId, taskIds, setTaskStatuses]);

  const handleGenerate = async () => {
    if (!selectedArea) {
      setError("Виберіть область на карті");
      return;
    }

    setError(null);
    setGenerating(true);

    try {
      const bounds = selectedArea;
      const request = {
        north: bounds.getNorth(),
        south: bounds.getSouth(),
        east: bounds.getEast(),
        west: bounds.getWest(),
        road_width_multiplier: roadWidthMultiplier,
        road_height_mm: roadHeightMm,
        road_embed_mm: roadEmbedMm,
        building_min_height: buildingMinHeight,
        building_height_multiplier: buildingHeightMultiplier,
        building_foundation_mm: buildingFoundationMm,
        building_embed_mm: buildingEmbedMm,
        water_depth: waterDepth,
        terrain_enabled: terrainEnabled,
        terrain_z_scale: terrainZScale,
        terrain_base_thickness_mm: terrainBaseThicknessMm,
        terrain_resolution: terrainResolution,
        terrarium_zoom: terrariumZoom,
        // Terrain-first стабілізація (backend default=true, але явно передаємо)
        flatten_buildings_on_terrain: true,
        export_format: exportFormat,
        model_size_mm: modelSizeMm,
      };

      const response = await api.generateModel(request);
      setTaskGroup(response.task_id, [response.task_id]);
      setActiveTaskId(response.task_id);
    } catch (err: any) {
      setError(err.message || "Помилка генерації моделі");
      setGenerating(false);
    }
  };

  const handleDownload = async () => {
    if (!activeTaskId || !downloadUrl) return;

    try {
      // Завжди качаємо рівно той формат, який вибрав користувач
      const blob = await api.downloadModel(activeTaskId, exportFormat);
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `model.${exportFormat}`;
      document.body.appendChild(a);
      a.click();
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    } catch (err) {
      setError("Помилка завантаження файлу");
    }
  };

  // Координати Києва за замовчуванням (lat, lon)
  const kyivBounds = {
    north: 50.6,   // Північна широта
    south: 50.2,   // Південна широта
    east: 30.8,    // Східна довгота
    west: 30.2,    // Західна довгота
  };

  const handleGenerateZones = async () => {
    if (selectedZones.length === 0) {
      setError("Виберіть хоча б одну зону");
      return;
    }

    setError(null);
    setGenerating(true);
    setShowHexGrid(false);

    try {
      // Використовуємо координати з вибраних зон
      const request = {
        north: kyivBounds.north,
        south: kyivBounds.south,
        east: kyivBounds.east,
        west: kyivBounds.west,
        road_width_multiplier: roadWidthMultiplier,
        road_height_mm: roadHeightMm,
        road_embed_mm: roadEmbedMm,
        building_min_height: buildingMinHeight,
        building_height_multiplier: buildingHeightMultiplier,
        building_foundation_mm: buildingFoundationMm,
        building_embed_mm: buildingEmbedMm,
        water_depth: waterDepth,
        terrain_enabled: terrainEnabled,
        terrain_z_scale: terrainZScale,
        terrain_base_thickness_mm: terrainBaseThicknessMm,
        terrain_resolution: terrainResolution,
        terrarium_zoom: terrariumZoom,
        terrain_smoothing_sigma: 2.0, // Значення за замовчуванням
        terrain_subdivide: false,
        terrain_subdivide_levels: 1,
        flatten_buildings_on_terrain: true,
        flatten_roads_on_terrain: false,
        export_format: exportFormat,
        model_size_mm: modelSizeMm,
      };

      const response = await api.generateZones(selectedZones, request);
      const ids = (response as any).all_task_ids && (response as any).all_task_ids.length
        ? (response as any).all_task_ids
        : [response.task_id];
      setTaskGroup(response.task_id, ids);
      setActiveTaskId(ids[0] ?? null);
    } catch (err: any) {
      setError(err.message || "Помилка генерації моделей для зон");
      setGenerating(false);
    }
  };

  return (
    <div className="p-4 space-y-6">
      <h1 className="text-2xl font-bold">3D Map Generator</h1>
      
      {/* Кнопка для відкриття гексагональної сітки */}
      <div className="flex gap-2 flex-wrap">
        <button
          onClick={() => {
            setShowHexGrid(!showHexGrid);
            setError(null);
          }}
          className="px-4 py-2 bg-green-500 text-white rounded hover:bg-green-600 flex items-center gap-2"
        >
          <Grid size={20} />
          {showHexGrid ? "Закрити сітку" : "Відкрити гексагональну сітку (Київ)"}
        </button>
        {showHexGrid && selectedZones.length > 0 && (
          <button
            onClick={handleGenerateZones}
            disabled={isGenerating}
            className="px-4 py-2 bg-blue-500 text-white rounded hover:bg-blue-600 disabled:bg-gray-400 flex items-center gap-2"
          >
            <Play size={20} />
            Генерувати для {selectedZones.length} зон
          </button>
        )}
        {showHexGrid && (
          <button
            onClick={() => setSelectedZones([])}
            className="px-4 py-2 bg-gray-500 text-white rounded hover:bg-gray-600 flex items-center gap-2"
          >
            Очистити вибір
          </button>
        )}
      </div>

      {/* Batch: список згенерованих зон (дає вибір, яку саме зону показувати/скачувати) */}
      {taskIds.length > 1 && (
        <div className="p-3 border rounded bg-white space-y-2">
          <div className="text-sm font-semibold">Згенеровані зони</div>
          <div className="text-xs text-gray-600">
            Оберіть зону нижче — превʼю і завантаження будуть для вибраної зони.
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => {
                const next = !showAllZones;
                setShowAllZones(next);
                setError(null);
                // тригеримо перезавантаження превʼю
                setDownloadUrl(null);
              }}
              className={`px-3 py-1 rounded text-xs ${
                showAllZones ? "bg-purple-600 text-white" : "bg-purple-100 text-purple-800 hover:bg-purple-200"
              }`}
            >
              {showAllZones ? "Показувати одну зону" : "Показати всі зони разом"}
            </button>
          </div>
          <div className="max-h-40 overflow-auto space-y-1">
            {taskIds.map((id) => (
              <button
                key={id}
                onClick={async () => {
                  // якщо увімкнено показ всіх зон — клік по окремій не змінює превʼю
                  if (showAllZones) return;
                  setActiveTaskId(id);
                  setError(null);

                  // Якщо статус уже відомий — одразу перемикаємо downloadUrl
                  const st = (taskStatuses as any)?.[id];
                  if (st && st.status === "completed" && st.download_url) {
                    setDownloadUrl(st.download_url);
                    return;
                  }

                  // Якщо ще генерується або статусів нема — очищаємо url і підтягуємо статус один раз
                  setDownloadUrl(null);
                  try {
                    const resp = await api.getStatus(id);
                    const single = resp as any;
                    if (single && single.status === "completed" && single.download_url) {
                      setDownloadUrl(single.download_url);
                    }
                  } catch {
                    // ignore
                  }
                }}
                className={`w-full text-left px-2 py-1 rounded text-sm ${
                  id === activeTaskId ? "bg-blue-100" : "hover:bg-gray-100"
                }`}
              >
                {id === activeTaskId ? "▶ " : ""}
                {id}
              </button>
            ))}
          </div>
        </div>
      )}


      {/* Параметри генерації */}
      <div className="space-y-4">
        <h2 className="text-lg font-semibold">Параметри генерації</h2>

        {/* Дороги */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Ширина доріг (множник): {roadWidthMultiplier.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.3"
            max="2.0"
            step="0.1"
            value={roadWidthMultiplier}
            onChange={(e) => setRoadWidthMultiplier(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Висота доріг (мм на моделі): {roadHeightMm.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.2"
            max="3.0"
            step="0.1"
            value={roadHeightMm}
            onChange={(e) => setRoadHeightMm(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Втиснення доріг у рельєф (мм): {roadEmbedMm.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.0"
            max="1.0"
            step="0.1"
            value={roadEmbedMm}
            onChange={(e) => setRoadEmbedMm(parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="text-xs text-gray-500 mt-1">
            Допомагає прибрати “висять/мерехтять” на стику з землею.
          </div>
        </div>

        {/* Будівлі */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Мінімальна висота будівлі (м): {buildingMinHeight.toFixed(1)}
          </label>
          <input
            type="range"
            min="1.0"
            max="10.0"
            step="0.5"
            value={buildingMinHeight}
            onChange={(e) => setBuildingMinHeight(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">
            Множник висоти будівель: {buildingHeightMultiplier.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.5"
            max="3.0"
            step="0.1"
            value={buildingHeightMultiplier}
            onChange={(e) => setBuildingHeightMultiplier(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Фундамент будівель (мм): {buildingFoundationMm.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.1"
            max="3.0"
            step="0.1"
            value={buildingFoundationMm}
            onChange={(e) => setBuildingFoundationMm(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">
            Втиснення будівель у землю (мм): {buildingEmbedMm.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.0"
            max="1.0"
            step="0.1"
            value={buildingEmbedMm}
            onChange={(e) => setBuildingEmbedMm(parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="text-xs text-gray-500 mt-1">
            Якщо будівлі “залазять під землю” — зменшуй. Якщо “висять” — збільшуй.
          </div>
        </div>

        {/* Вода */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Глибина води (мм): {waterDepth.toFixed(1)}
          </label>
          <input
            type="range"
            min="0.5"
            max="5.0"
            step="0.5"
            value={waterDepth}
            onChange={(e) => setWaterDepth(parseFloat(e.target.value))}
            className="w-full"
          />
        </div>

        {/* Рельєф */}
        <div className="flex items-center space-x-2">
          <input
            type="checkbox"
            id="terrain"
            checked={terrainEnabled}
            onChange={(e) => setTerrainEnabled(e.target.checked)}
            className="w-4 h-4"
          />
          <label htmlFor="terrain" className="text-sm font-medium">
            Увімкнути рельєф
          </label>
        </div>

        {terrainEnabled && (
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium mb-1">
                Множник висоти рельєфу: {terrainZScale.toFixed(1)}
              </label>
              <input
                type="range"
                min="0.5"
                max="3.0"
                step="0.1"
                value={terrainZScale}
                onChange={(e) => setTerrainZScale(parseFloat(e.target.value))}
                className="w-full"
              />
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Деталізація рельєфу (mesh): {terrainResolution}×{terrainResolution}
              </label>
              <input
                type="range"
                min="120"
                max="320"
                step="20"
                value={terrainResolution}
                onChange={(e) => setTerrainResolution(parseInt(e.target.value, 10))}
                className="w-full"
              />
              <div className="text-xs text-gray-500 mt-1">
                Більше = детальніше, але повільніше генерує.
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Terrarium zoom (DEM tiles): {terrariumZoom}
              </label>
              <input
                type="range"
                min="11"
                max="16"
                step="1"
                value={terrariumZoom}
                onChange={(e) => setTerrariumZoom(parseInt(e.target.value, 10))}
                className="w-full"
              />
              <div className="text-xs text-gray-500 mt-1">
                14–15 рекомендовано. 16 може бути повільно (багато тайлів).
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium mb-1">
                Товщина основи рельєфу (мм): {terrainBaseThicknessMm.toFixed(1)}
              </label>
              <input
                type="range"
                min="1.0"
                max="12.0"
                step="0.5"
                value={terrainBaseThicknessMm}
                onChange={(e) => setTerrainBaseThicknessMm(parseFloat(e.target.value))}
                className="w-full"
              />
              <div className="text-xs text-gray-500 mt-1">
                Робить “цеглину”, а не “листок” — важливо для 3D-друку.
              </div>
            </div>
          </div>
        )}

        {/* Формат експорту */}
        <div>
          <label className="block text-sm font-medium mb-1">Формат експорту</label>
          <select
            value={exportFormat}
            onChange={(e) => setExportFormat(e.target.value as "stl" | "3mf")}
            className="w-full p-2 border rounded"
          >
            <option value="3mf">3MF (рекомендовано)</option>
            <option value="stl">STL</option>
          </select>
        </div>

        {/* Розмір моделі */}
        <div>
          <label className="block text-sm font-medium mb-1">
            Розмір моделі: {modelSizeMm.toFixed(0)} мм ({(modelSizeMm / 10).toFixed(1)} см)
          </label>
          <input
            type="range"
            min="50"
            max="500"
            step="10"
            value={modelSizeMm}
            onChange={(e) => setModelSizeMm(parseFloat(e.target.value))}
            className="w-full"
          />
          <div className="flex justify-between text-xs text-gray-500 mt-1">
            <span>50 мм (5 см)</span>
            <span>250 мм (25 см)</span>
            <span>500 мм (50 см)</span>
          </div>
        </div>
      </div>

      {/* Кнопка генерації */}
      <button
        onClick={handleGenerate}
        disabled={!selectedArea || isGenerating}
        className="w-full bg-blue-600 text-white py-2 px-4 rounded hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed flex items-center justify-center space-x-2"
      >
        {isGenerating ? (
          <>
            <Loader2 className="w-4 h-4 animate-spin" />
            <span>Генерація...</span>
          </>
        ) : (
          <>
            <Play className="w-4 h-4" />
            <span>Згенерувати модель</span>
          </>
        )}
      </button>

      {/* Прогрес */}
      {isGenerating && (
        <div className="space-y-2">
          <div className="w-full bg-gray-200 rounded-full h-2">
            <div
              className="bg-blue-600 h-2 rounded-full transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="text-sm text-gray-600">{status || "Обробка..."}</p>
        </div>
      )}

      {/* Завантаження */}
      {downloadUrl && (
        <button
          onClick={handleDownload}
          className="w-full bg-green-600 text-white py-2 px-4 rounded hover:bg-green-700 flex items-center justify-center space-x-2"
        >
          <Download className="w-4 h-4" />
          <span>Завантажити модель</span>
        </button>
      )}

      {/* Помилка */}
      {error && (
        <div className="bg-red-100 border border-red-400 text-red-700 px-4 py-3 rounded">
          {error}
        </div>
      )}
    </div>
  );
}

