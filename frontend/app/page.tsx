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
  const [gridType, setGridType] = useState<"hexagonal" | "square">("hexagonal");
  const [hexSizeM, setHexSizeM] = useState(500.0);
  
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
          gridType={gridType}
          setGridType={setGridType}
          hexSizeM={hexSizeM}
          setHexSizeM={setHexSizeM}
        />
      </div>

      {/* Основна область */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* Карта - замінюємо на HexagonalGrid якщо showHexGrid = true */}
        <div className="h-1/2 border-b border-gray-300 dark:border-gray-700 min-h-0">
          {showHexGrid ? (
            <HexagonalGrid
              key={`hex-grid-${gridType}-${hexSizeM}`} // Key для перемонтування при зміні налаштувань
              bounds={kyivBounds}
              onZonesSelected={setSelectedZones}
              gridType={gridType}
              hexSizeM={hexSizeM}
            />
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

