"use client";

import dynamic from "next/dynamic";
import { Preview3D } from "@/components/Preview3D";
import { ControlPanel } from "@/components/ControlPanel";

// Динамічний імпорт з вимкненим SSR для Leaflet
const MapSelector = dynamic(() => import("@/components/MapSelector").then(mod => ({ default: mod.MapSelector })), {
  ssr: false,
  loading: () => <div className="w-full h-full flex items-center justify-center bg-gray-200">Завантаження карти...</div>
});

export default function Home() {
  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* Бічна панель з налаштуваннями */}
      <div className="w-80 bg-gray-100 dark:bg-gray-900 border-r border-gray-300 dark:border-gray-700 overflow-y-auto">
        <ControlPanel />
      </div>

      {/* Основна область */}
      <div className="flex-1 flex flex-col min-h-0">
        {/* Карта */}
        <div className="h-1/2 border-b border-gray-300 dark:border-gray-700 min-h-0">
          <MapSelector />
        </div>

        {/* 3D Прев'ю */}
        <div className="h-1/2 min-h-0 flex-shrink-0">
          <Preview3D />
        </div>
      </div>
    </div>
  );
}

