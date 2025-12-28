"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { Preview3D } from "@/components/Preview3D";
import { ControlPanel } from "@/components/ControlPanel";

// Динамічний імпорт з вимкненим SSR для Leaflet
const MapSelector = dynamic(() => import("@/components/MapSelector").then(mod => ({ default: mod.MapSelector })), {
  ssr: false,
  loading: () => <div className="w-full h-full flex items-center justify-center bg-gray-200">Завантаження карти...</div>
});

const HexagonalGrid = dynamic(() => import("@/components/HexagonalGrid"), {
  ssr: false,
  loading: () => <div className="w-full h-full flex items-center justify-center bg-gray-200">Завантаження сітки...</div>
});

export default function Home() {
  const [showHexGrid, setShowHexGrid] = useState(false);
  const [selectedZones, setSelectedZones] = useState<any[]>([]);
  
  // Координати Києва
  const kyivBounds = {
    north: 50.6,
    south: 50.2,
    east: 30.8,
    west: 30.2,
  };

  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Бічна панель з налаштуваннями */}
      <div className="w-80 bg-gray-100 dark:bg-gray-900 border-r border-gray-300 dark:border-gray-700 overflow-y-auto">
        <ControlPanel 
          showHexGrid={showHexGrid}
          setShowHexGrid={setShowHexGrid}
          selectedZones={selectedZones}
          setSelectedZones={setSelectedZones}
        />
      </div>

      {/* Основна область */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* Карта - замінюємо на HexagonalGrid якщо showHexGrid = true */}
        <div className="h-1/2 border-b border-gray-300 dark:border-gray-700 min-h-0">
          {showHexGrid ? (
            <div className="w-full h-full relative">
              <div className="absolute top-2 left-2 z-[1000] bg-white p-2 rounded shadow-lg">
                <button
                  onClick={() => {
                    setShowHexGrid(false);
                    setSelectedZones([]);
                  }}
                  className="px-3 py-1 bg-gray-500 text-white rounded hover:bg-gray-600 text-sm"
                >
                  ✕ Закрити сітку
                </button>
                {selectedZones.length > 0 && (
                  <div className="mt-2 text-sm space-y-1">
                    <p className="font-semibold text-blue-600">Вибрано: {selectedZones.length} зон</p>
                    <p className="text-xs text-gray-600">Клікніть по зонах на карті для вибору/зняття вибору</p>
                  </div>
                )}
              </div>
              <HexagonalGrid
                key="hex-grid-kyiv" // Key для перемонтування при відкритті
                bounds={kyivBounds}
                onZonesSelected={setSelectedZones}
              />
            </div>
          ) : (
            <MapSelector />
          )}
        </div>

        {/* 3D Прев'ю */}
        <div className="h-1/2 min-h-0 flex-shrink-0">
          <Preview3D />
        </div>
      </div>
    </div>
  );
}

