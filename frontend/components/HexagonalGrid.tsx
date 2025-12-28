"use client";

import { useState, useEffect } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Компонент для автоматичного fitBounds
function MapBounds({ bounds }: { bounds: { north: number; south: number; east: number; west: number } }) {
  const map = useMap();
  useEffect(() => {
    if (bounds && map) {
      try {
        map.fitBounds([
          [bounds.south, bounds.west],
          [bounds.north, bounds.east],
        ] as L.LatLngBoundsExpression, {
          padding: [20, 20],
          maxZoom: 13,
        });
      } catch (e) {
        console.error("Помилка fitBounds:", e);
      }
    }
  }, [map, bounds]);
  return null;
}

interface HexagonalGridProps {
  bounds: {
    north: number;
    south: number;
    east: number;
    west: number;
  };
  onZonesSelected: (zones: any[]) => void;
}

// Стилі для шестикутників
const defaultStyle = {
  color: "#3388ff",
  weight: 2,
  opacity: 0.7,
  fillOpacity: 0.1,
};

const selectedStyle = {
  color: "#ff0000",
  weight: 4,
  opacity: 1,
  fillOpacity: 0.5,
  fillColor: "#ff0000",
};

const hoverStyle = {
  color: "#00ff00",
  weight: 2,
  opacity: 0.9,
  fillOpacity: 0.2,
};

export default function HexagonalGrid({ bounds, onZonesSelected }: HexagonalGridProps) {
  const [hexGrid, setHexGrid] = useState<any>(null);
  const [selectedZones, setSelectedZones] = useState<Set<string>>(new Set());
  const [hoveredZone, setHoveredZone] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isValid, setIsValid] = useState(true);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [gridType, setGridType] = useState<"hexagonal" | "square">("hexagonal");

  const generateGrid = async () => {
    if (isLoading) return; // Запобігаємо подвійній генерації
    
    setIsLoading(true);
    setHexGrid(null); // Скидаємо попередню сітку
    
    try {
      console.log("[HexagonalGrid] Запит генерації сітки з bounds:", bounds);
      const { api } = await import("@/lib/api");
      
      // Перевіряємо валідність bounds
      if (!bounds || bounds.north <= bounds.south || bounds.east <= bounds.west) {
        throw new Error(`Невірні координати bounds: north=${bounds?.north}, south=${bounds?.south}, east=${bounds?.east}, west=${bounds?.west}`);
      }
      
      console.log("[HexagonalGrid] Відправляємо запит до API з bounds:", bounds);
      const data = await api.generateHexagonalGrid({
        north: bounds.north,
        south: bounds.south,
        east: bounds.east,
        west: bounds.west,
        hex_size_m: 1000.0, // 1 км
        grid_type: gridType,
      });
      
      console.log("[HexagonalGrid] Отримано сітку:", data.hex_count, "шестикутників, is_valid:", data.is_valid);
      
      if (!data.geojson || !data.geojson.features || data.geojson.features.length === 0) {
        throw new Error("Сітка порожня або невалідна");
      }
      
      // Діагностика першого feature
      if (data.geojson.features.length > 0) {
        const firstFeature = data.geojson.features[0];
        const firstCoords = firstFeature?.geometry?.coordinates?.[0]?.[0];
        console.log("[HexagonalGrid] Перший шестикутник:", {
          id: firstFeature?.id,
          type: firstFeature?.geometry?.type,
          firstCoord: firstCoords,
          coordsCount: firstFeature?.geometry?.coordinates?.[0]?.length
        });
      }
      
      setHexGrid(data.geojson);
      setIsValid(data.is_valid);
      setValidationErrors(data.validation_errors || []);
    } catch (error: any) {
      console.error("Помилка генерації сітки:", error);
      const errorMessage = error.response?.data?.detail || error.message || String(error);
      alert("Помилка генерації сітки: " + errorMessage);
      setHexGrid(null);
    } finally {
      setIsLoading(false);
    }
  };

  const handleZoneClick = (zoneId: string, feature: any, event?: L.LeafletMouseEvent) => {
    console.log(`[HexagonalGrid] handleZoneClick called for zoneId: ${zoneId}, current selected:`, Array.from(selectedZones));
    
    if (!zoneId) {
      console.error("[HexagonalGrid] zoneId is empty!");
      return;
    }
    
    const newSelected = new Set(selectedZones);
    
    // Перемикаємо стан зони
    if (newSelected.has(zoneId)) {
      newSelected.delete(zoneId);
      console.log(`[HexagonalGrid] Zone ${zoneId} deselected. Total selected: ${newSelected.size}`);
    } else {
      newSelected.add(zoneId);
      console.log(`[HexagonalGrid] Zone ${zoneId} selected. Total selected: ${newSelected.size}`);
    }
    setSelectedZones(newSelected);
    
    // Оновлюємо список вибраних зон - використовуємо правильний ID
    const selectedFeatures = hexGrid.features.filter((f: any) => {
      const fId = f.id || f.properties?.id;
      return newSelected.has(fId);
    });
    
    console.log(`[HexagonalGrid] Selected features count: ${selectedFeatures.length}`);
    onZonesSelected(selectedFeatures);
  };

  const handleSelectAll = () => {
    if (!hexGrid || !hexGrid.features) return;
    const allZoneIds = new Set(hexGrid.features.map((f: any) => f.id || f.properties?.id));
    setSelectedZones(allZoneIds);
    onZonesSelected(hexGrid.features);
    console.log(`[HexagonalGrid] All ${allZoneIds.size} zones selected`);
  };

  const handleDeselectAll = () => {
    setSelectedZones(new Set());
    onZonesSelected([]);
    console.log("[HexagonalGrid] All zones deselected");
  };

  const handleZoneHover = (zoneId: string | null) => {
    setHoveredZone(zoneId);
  };

  const getZoneStyle = (zoneId: string) => {
    if (!zoneId) {
      console.warn("[HexagonalGrid] getZoneStyle called with empty zoneId");
      return defaultStyle;
    }
    
    const isSelected = selectedZones.has(zoneId);
    const isHovered = hoveredZone === zoneId;
    
    if (isSelected) {
      return selectedStyle;
    }
    if (isHovered) {
      return hoverStyle;
    }
    return defaultStyle;
  };

  const center: [number, number] = [
    (bounds.north + bounds.south) / 2,
    (bounds.east + bounds.west) / 2,
  ];

  // Автоматично генеруємо сітку при відкритті
  useEffect(() => {
    console.log("[HexagonalGrid] useEffect викликано, bounds:", bounds, "hexGrid:", !!hexGrid, "isLoading:", isLoading, "gridType:", gridType);
    
    // Перевіряємо валідність bounds
    if (!bounds) {
      console.warn("[HexagonalGrid] bounds не визначено");
      return;
    }
    
    if (bounds.north <= bounds.south || bounds.east <= bounds.west) {
      console.error("[HexagonalGrid] Невірні координати:", bounds);
      return;
    }
    
    // Генеруємо сітку якщо вона ще не згенерована
    if (!hexGrid && !isLoading) {
      console.log("[HexagonalGrid] Запускаємо генерацію сітки для bounds:", bounds);
      // Невелика затримка для гарантії, що компонент повністю змонтований
      const timer = setTimeout(() => {
        generateGrid();
      }, 200);
      return () => clearTimeout(timer);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bounds?.north, bounds?.south, bounds?.east, bounds?.west, hexGrid, isLoading, gridType]); // перегенеруємо при зміні bounds/gridType

  const zoom = 11; // Оптимальний zoom для Києва

  return (
    <div className="w-full h-full flex flex-col">
      <div className="p-3 bg-gray-50 border-b flex-shrink-0">
        {isLoading ? (
          <div className="flex items-center gap-2 text-sm">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-500"></div>
            <span>Генерація сітки...</span>
          </div>
        ) : hexGrid ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <label className="text-xs font-semibold text-gray-700">Тип сітки:</label>
              <select
                className="text-xs border rounded px-2 py-1"
                value={gridType}
                onChange={(e) => {
                  const next = e.target.value as "hexagonal" | "square";
                  // скидаємо вибір, щоб не було міксу id/feature різних сіток
                  setSelectedZones(new Set());
                  onZonesSelected([]);
                  setHexGrid(null);
                  setGridType(next);
                }}
              >
                <option value="hexagonal">Шестикутники</option>
                <option value="square">Квадрати</option>
              </select>
            </div>
            <div className="text-xs space-y-1">
              <p className="font-semibold">Клітинок: {hexGrid.features.length}</p>
              <p className="font-semibold text-blue-600">Вибрано: {selectedZones.size}</p>
              {selectedZones.size > 0 && (
                <p className="text-green-600 font-medium">Готово до генерації!</p>
              )}
              {!isValid && validationErrors.length > 0 && (
                <div className="text-red-500 mt-1 text-xs">
                  <p>Попередження: {validationErrors.length} помилок</p>
                </div>
              )}
            </div>
            <div className="space-y-2">
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={handleSelectAll}
                  className="px-2 py-1 text-xs bg-blue-500 text-white rounded hover:bg-blue-600"
                  title="Вибрати всі зони"
                >
                  Вибрати всі
                </button>
                <button
                  onClick={handleDeselectAll}
                  className="px-2 py-1 text-xs bg-gray-500 text-white rounded hover:bg-gray-600"
                  title="Зняти вибір з усіх зон"
                >
                  Зняти вибір
                </button>
              </div>
              <div className="text-xs text-gray-600 space-y-1 border-t pt-2">
                <p className="font-semibold">Як вибрати зони:</p>
                <ul className="list-disc list-inside space-y-0.5 text-[10px]">
                  <li>Клікніть по зоні для вибору/зняття вибору</li>
                  <li>Можна вибрати будь-яку кількість зон</li>
                  <li>Вибрані зони виділені <span className="text-red-600 font-bold">червоним</span> кольором</li>
                </ul>
              </div>
            </div>
          </div>
        ) : (
          <div className="text-xs text-gray-600">Генерація сітки...</div>
        )}
      </div>

      <div className="flex-1 relative min-h-0">
        <MapContainer
          center={center}
          zoom={zoom}
          style={{ height: "100%", width: "100%" }}
          scrollWheelZoom={true}
          whenReady={() => {
            // Карта готова
          }}
        >
          <TileLayer
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
          />
          <MapBounds bounds={bounds} />
          
          {hexGrid && hexGrid.features && hexGrid.features.length > 0 && (
            <GeoJSON
              key={`hex-grid-${hexGrid.features.length}-${selectedZones.size}`}
              data={hexGrid}
              style={(feature) => {
                const zoneId = feature?.properties?.id || feature?.id;
                if (!zoneId) {
                  console.warn("[HexagonalGrid] Feature without ID in style function:", feature);
                  return defaultStyle;
                }
                const style = getZoneStyle(zoneId);
                return style;
              }}
              onEachFeature={(feature, layer) => {
                const zoneId = feature?.properties?.id || feature?.id;
                
                if (!zoneId) {
                  console.error("[HexagonalGrid] Feature without ID:", feature);
                  return;
                }
                
                console.log(`[HexagonalGrid] Feature ${zoneId} added to map, feature.id=${feature?.id}, feature.properties.id=${feature?.properties?.id}`);
                
                // Зберігаємо посилання на layer для оновлення стилю
                (layer as any)._hexZoneId = zoneId;
                
                layer.on({
                  click: (e: L.LeafletMouseEvent) => {
                    e.originalEvent?.stopPropagation?.();
                    console.log(`[HexagonalGrid] Zone ${zoneId} clicked, event:`, e);
                    handleZoneClick(zoneId, feature, e);
                    
                    // Оновлюємо стиль після кліку
                    setTimeout(() => {
                      layer.setStyle(getZoneStyle(zoneId));
                    }, 0);
                  },
                  mouseover: () => {
                    handleZoneHover(zoneId);
                    layer.setStyle(hoverStyle);
                  },
                  mouseout: () => {
                    handleZoneHover(null);
                    layer.setStyle(getZoneStyle(zoneId));
                  },
                });

                // Додаємо popup з інформацією
                const props = feature.properties || {};
                const isSelected = selectedZones.has(zoneId);
                layer.bindPopup(
                  `<b>Зона ${zoneId}</b><br/>Ряд: ${props.row}, Колонка: ${props.col}<br/>${isSelected ? '<span style="color: red; font-weight: bold;">✓ Вибрано</span>' : '<span style="color: gray;">Клікніть для вибору</span>'}`
                );
              }}
            />
          )}
        </MapContainer>
      </div>
    </div>
  );
}

