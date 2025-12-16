"use client";

import { useState, useEffect } from "react";
import { useGenerationStore } from "@/store/generation-store";
import { api } from "@/lib/api";
import { Download, Loader2, Play } from "lucide-react";

export function ControlPanel() {
  const {
    selectedArea,
    isGenerating,
    taskId,
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
    setTaskId,
    updateProgress,
    setDownloadUrl,
  } = useGenerationStore();

  const [error, setError] = useState<string | null>(null);

  // Перевірка статусу задачі
  useEffect(() => {
    if (!taskId || !isGenerating) return;

    const interval = setInterval(async () => {
      try {
        const status = await api.getStatus(taskId);
        updateProgress(status.progress, status.message);

        if (status.status === "completed") {
          setGenerating(false);
          setDownloadUrl(status.download_url);
        } else if (status.status === "failed") {
          setGenerating(false);
          setError(status.message);
        }
      } catch (err) {
        console.error("Помилка перевірки статусу:", err);
      }
    }, 2000); // Перевірка кожні 2 секунди

    return () => clearInterval(interval);
  }, [taskId, isGenerating, updateProgress, setGenerating, setDownloadUrl]);

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
      setTaskId(response.task_id);
    } catch (err: any) {
      setError(err.message || "Помилка генерації моделі");
      setGenerating(false);
    }
  };

  const handleDownload = async () => {
    if (!taskId || !downloadUrl) return;

    try {
      // Завжди качаємо рівно той формат, який вибрав користувач
      const blob = await api.downloadModel(taskId, exportFormat);
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

  return (
    <div className="p-4 space-y-6">
      <h1 className="text-2xl font-bold">3D Map Generator</h1>

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

