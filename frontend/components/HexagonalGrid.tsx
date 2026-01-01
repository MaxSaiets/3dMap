"use client";

import { useState, useEffect, useRef } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Компонент для автоматичного fitBounds (тільки при першому завантаженні)
function MapBounds({ bounds }: { bounds: { north: number; south: number; east: number; west: number } }) {
  const map = useMap();
  const hasFittedRef = useRef(false);
  
  useEffect(() => {
    if (bounds && map && !hasFittedRef.current) {
      try {
        map.fitBounds([
          [bounds.south, bounds.west],
          [bounds.north, bounds.east],
        ] as L.LatLngBoundsExpression, {
          padding: [20, 20],
          maxZoom: 13,
        });
        hasFittedRef.current = true; // Виконуємо тільки один раз
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
  gridType?: "hexagonal" | "square";
  hexSizeM?: number;
}

// Стилі для шестикутників
const defaultStyle = {
  color: "#3388ff",
  weight: 1.5,
  opacity: 0.8,
  fillOpacity: 0.15,
};

const selectedStyle = {
  color: "#dc2626",
  weight: 3,
  opacity: 1,
  fillOpacity: 0.6,
  fillColor: "#ef4444",
};

const hoverStyle = {
  color: "#10b981",
  weight: 2.5,
  opacity: 1,
  fillOpacity: 0.3,
  fillColor: "#34d399",
};

export default function HexagonalGrid({ 
  bounds, 
  onZonesSelected,
  gridType: externalGridType = "hexagonal",
  hexSizeM: externalHexSizeM = 500.0,
}: HexagonalGridProps) {
  const normalizeId = (id: any): string => String(id ?? "");
  const [hexGrid, setHexGrid] = useState<any>(null);
  const [selectedZones, setSelectedZones] = useState<Set<string>>(new Set());
  // Ordered selection (so zones can be generated and previewed "one after another")
  const [selectedOrder, setSelectedOrder] = useState<string[]>([]);
  const [hoveredZone, setHoveredZone] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isValid, setIsValid] = useState(true);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);

  // IMPORTANT: Leaflet feature handlers are attached once and keep stale React closures.
  // Use refs as the source of truth for click/hover handlers.
  const hexGridRef = useRef<any>(null);
  const selectedZonesRef = useRef<Set<string>>(new Set());
  const selectedOrderRef = useRef<string[]>([]);
  const hoveredZoneRef = useRef<string | null>(null);
  const onZonesSelectedRef = useRef(onZonesSelected);

  useEffect(() => {
    hexGridRef.current = hexGrid;
  }, [hexGrid]);
  useEffect(() => {
    selectedZonesRef.current = new Set(selectedZones);
  }, [selectedZones]);
  useEffect(() => {
    selectedOrderRef.current = [...selectedOrder];
  }, [selectedOrder]);
  useEffect(() => {
    hoveredZoneRef.current = hoveredZone;
  }, [hoveredZone]);
  useEffect(() => {
    onZonesSelectedRef.current = onZonesSelected;
  }, [onZonesSelected]);
  
  // Використовуємо зовнішні значення якщо передані, інакше внутрішні
  const [internalGridType, setInternalGridType] = useState<"hexagonal" | "square">("hexagonal");
  const gridType = externalGridType || internalGridType;
  const hexSizeM = externalHexSizeM || 500.0;

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
      
      console.log("[HexagonalGrid] Відправляємо запит до API з bounds:", bounds, "gridType:", gridType, "hexSizeM:", hexSizeM);
      const data = await api.generateHexagonalGrid({
        north: bounds.north,
        south: bounds.south,
        east: bounds.east,
        west: bounds.west,
        hex_size_m: hexSizeM,
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

  const handleZoneClick = (zoneIdRaw: any) => {
    const zoneId = normalizeId(zoneIdRaw);
    const currentSelected = selectedZonesRef.current;
    const currentOrder = selectedOrderRef.current;
    console.log(`[HexagonalGrid] handleZoneClick called for zoneId: ${zoneId}, current selected:`, Array.from(currentSelected));
    
    if (!zoneId) {
      console.error("[HexagonalGrid] zoneId is empty!");
      return;
    }
    
    const nextSelected = new Set(currentSelected);
    let nextOrder = [...currentOrder];
    
    // Перемикаємо стан зони
    if (nextSelected.has(zoneId)) {
      nextSelected.delete(zoneId);
      nextOrder = nextOrder.filter((id) => id !== zoneId);
      console.log(`[HexagonalGrid] Zone ${zoneId} deselected. Total selected: ${nextSelected.size}`);
    } else {
      nextSelected.add(zoneId);
      // Add to the end to preserve click order
      if (!nextOrder.includes(zoneId)) nextOrder.push(zoneId);
      console.log(`[HexagonalGrid] Zone ${zoneId} selected. Total selected: ${nextSelected.size}`);
    }
    // Sync refs immediately (so next click sees updated state even before React renders)
    selectedZonesRef.current = nextSelected;
    selectedOrderRef.current = nextOrder;
    setSelectedZones(nextSelected);
    setSelectedOrder(nextOrder);
    
    // Оновлюємо список вибраних зон у стабільному порядку (click-order),
    // щоб backend створював задачі у тій же послідовності.
    const featureById = new Map<string, any>();
    for (const f of (hexGridRef.current?.features || [])) {
      const fId = normalizeId(f.id || f.properties?.id);
      if (fId) featureById.set(fId, f);
    }
    const selectedFeatures = nextOrder.map((id) => featureById.get(id)).filter(Boolean);
    
    console.log(`[HexagonalGrid] Selected features count: ${selectedFeatures.length}`);
    onZonesSelectedRef.current(selectedFeatures);
  };

  const handleSelectAll = () => {
    if (!hexGrid || !hexGrid.features) return;
    const all = (hexGrid.features || [])
      .map((f: any) => ({ id: normalizeId(f.id || f.properties?.id), feature: f }))
      .filter((x: any) => !!x.id);
    // Default order: by row/col if present (better UX for "in a row" selections), else original order
    all.sort((a: any, b: any) => {
      const ar = a.feature?.properties?.row;
      const br = b.feature?.properties?.row;
      const ac = a.feature?.properties?.col;
      const bc = b.feature?.properties?.col;
      if (ar != null && br != null && ar !== br) return ar - br;
      if (ac != null && bc != null && ac !== bc) return ac - bc;
      return String(a.id).localeCompare(String(b.id));
    });
    const allZoneIds = new Set(all.map((x: any) => x.id));
    selectedZonesRef.current = allZoneIds;
    selectedOrderRef.current = all.map((x: any) => x.id);
    setSelectedZones(allZoneIds);
    setSelectedOrder(all.map((x: any) => x.id));
    onZonesSelectedRef.current(all.map((x: any) => x.feature));
    console.log(`[HexagonalGrid] All ${allZoneIds.size} zones selected`);
  };

  const handleDeselectAll = () => {
    selectedZonesRef.current = new Set();
    selectedOrderRef.current = [];
    setSelectedZones(new Set());
    setSelectedOrder([]);
    onZonesSelectedRef.current([]);
    console.log("[HexagonalGrid] All zones deselected");
  };

  const handleZoneHover = (zoneId: string | null) => {
    hoveredZoneRef.current = zoneId;
    setHoveredZone(zoneId);
  };

  const getZoneStyle = (zoneId: string) => {
    const zid = normalizeId(zoneId);
    if (!zid) {
      console.warn("[HexagonalGrid] getZoneStyle called with empty zoneId");
      return defaultStyle;
    }
    
    const isSelected = selectedZonesRef.current.has(zid);
    const isHovered = hoveredZoneRef.current === zid;
    
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
  }, [bounds?.north, bounds?.south, bounds?.east, bounds?.west, hexGrid, isLoading, gridType, hexSizeM]); // перегенеруємо при зміні bounds/gridType/hexSizeM

  const zoom = 11; // Оптимальний zoom для Києва

  return (
    <div className="w-full h-full flex flex-col">
      <div className="px-2 py-1.5 bg-white border-b border-gray-200 flex-shrink-0 shadow-sm">
        {isLoading ? (
          <div className="flex items-center gap-1.5 text-[11px]">
            <div className="animate-spin rounded-full h-3 w-3 border-b-2 border-blue-500"></div>
            <span className="text-gray-700">Генерація сітки...</span>
          </div>
        ) : hexGrid ? (
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3 text-[11px]">
              <span className="font-medium text-gray-700">
                Клітинок: <span className="text-gray-900 font-semibold">{hexGrid.features.length}</span>
              </span>
              <span className="font-medium text-blue-700">
                Вибрано: <span className="text-blue-800 font-bold">{selectedZones.size}</span>
              </span>
              {selectedZones.size > 0 && (
                <span className="text-green-700 font-semibold">✓ Готово</span>
              )}
              {!isValid && validationErrors.length > 0 && (
                <span className="text-red-600 text-[10px]">
                  ⚠ {validationErrors.length} помилок
                </span>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <button
                onClick={handleSelectAll}
                className="px-2 py-0.5 text-[10px] bg-blue-500 text-white rounded hover:bg-blue-600 transition-colors"
                title="Вибрати всі зони"
              >
                Всі
              </button>
              <button
                onClick={handleDeselectAll}
                className="px-2 py-0.5 text-[10px] bg-gray-500 text-white rounded hover:bg-gray-600 transition-colors"
                title="Зняти вибір з усіх зон"
              >
                Очистити
              </button>
            </div>
          </div>
        ) : (
          <div className="text-[11px] text-gray-600">Генерація сітки...</div>
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
              // IMPORTANT: Do NOT remount on selection changes, otherwise the map/tiles can "jump back".
              key={`hex-grid-${hexGrid.features.length}-${gridType}-${hexSizeM}`}
              data={hexGrid}
              style={(feature) => {
                const zoneId = normalizeId(feature?.properties?.id || feature?.id);
                if (!zoneId) {
                  console.warn("[HexagonalGrid] Feature without ID in style function:", feature);
                  return defaultStyle;
                }
                const style = getZoneStyle(zoneId);
                return style;
              }}
              onEachFeature={(feature, layer) => {
                const zoneId = normalizeId(feature?.properties?.id || feature?.id);
                
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
                    e.originalEvent?.preventDefault?.();
                    console.log(`[HexagonalGrid] Zone ${zoneId} clicked, event:`, e);
                    // Apply immediate visual feedback based on ref state (no stale closures)
                    const willSelect = !selectedZonesRef.current.has(zoneId);
                    handleZoneClick(zoneId);
                    
                    // Оновлюємо стиль після кліку
                    setTimeout(() => {
                      // Use immediate decision first, then fallback to state-driven style
                      layer.setStyle(willSelect ? selectedStyle : defaultStyle);
                      // After state settles, sync with computed style
                      setTimeout(() => layer.setStyle(getZoneStyle(zoneId)), 0);
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

                // Додаємо popup з інформацією (тільки при наведенні, не при кліку)
                const props = feature.properties || {};
                const isSelected = selectedZones.has(zoneId);
                layer.bindTooltip(
                  `<b>Зона ${zoneId}</b><br/>Ряд: ${props.row}, Колонка: ${props.col}<br/>${isSelected ? '<span style="color: red; font-weight: bold;">✓ Вибрано</span>' : '<span style="color: gray;">Клікніть для вибору</span>'}`,
                  {
                    permanent: false,
                    direction: 'top',
                    offset: [0, -10],
                    className: 'zone-tooltip'
                  }
                );
              }}
            />
          )}
        </MapContainer>
      </div>
    </div>
  );
}

