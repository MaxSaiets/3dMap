"use client";

import { useEffect, useRef } from "react";
import { MapContainer, TileLayer, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet-draw";
import { useGenerationStore } from "@/store/generation-store";

// Виправлення іконок Leaflet для Next.js (тільки на клієнті)
if (typeof window !== "undefined") {
  delete (L.Icon.Default.prototype as any)._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png",
    iconUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png",
    shadowUrl: "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
  });
}

function DrawControl() {
  const map = useMap();
  const drawnItemsRef = useRef<L.FeatureGroup>(new L.FeatureGroup());
  const { setSelectedArea } = useGenerationStore();

  useEffect(() => {
    if (!map) return;

    map.addLayer(drawnItemsRef.current);

    const drawControl = new L.Control.Draw({
      position: "topright",
      draw: {
        rectangle: {
          shapeOptions: {
            color: "#3388ff",
            weight: 2,
          },
        },
        polygon: {
          shapeOptions: {
            color: "#3388ff",
            weight: 2,
          },
        },
        circle: {
          shapeOptions: {
            color: "#3388ff",
            weight: 2,
          },
        },
        marker: false,
        circlemarker: false,
        polyline: false,
      },
      edit: {
        featureGroup: drawnItemsRef.current,
        remove: true,
      },
    });

    map.addControl(drawControl);

    const handleDrawCreated = (e: any) => {
      const layer = e.layer;
      drawnItemsRef.current.addLayer(layer);

      // Отримуємо bounds обраної області
      if ("getBounds" in (layer as any) && typeof (layer as any).getBounds === "function") {
        const bounds = (layer as L.Rectangle | L.Polygon | L.Circle).getBounds();
        setSelectedArea(bounds);
      } else {
        // На випадок неочікуваних layer типів
        console.warn("Draw created layer does not support getBounds:", layer);
      }
    };

    const handleDrawEdited = () => {
      const layers = drawnItemsRef.current.getLayers();
      if (layers.length > 0) {
        const layer = layers[0] as L.Layer;
        if ("getBounds" in layer) {
          const bounds = (layer as L.Rectangle | L.Polygon | L.Circle).getBounds();
          setSelectedArea(bounds);
        }
      }
    };

    const handleDrawDeleted = () => {
      setSelectedArea(null);
    };

    map.on(L.Draw.Event.CREATED, handleDrawCreated);
    map.on(L.Draw.Event.EDITED, handleDrawEdited);
    map.on(L.Draw.Event.DELETED, handleDrawDeleted);

    return () => {
      map.off(L.Draw.Event.CREATED, handleDrawCreated);
      map.off(L.Draw.Event.EDITED, handleDrawEdited);
      map.off(L.Draw.Event.DELETED, handleDrawDeleted);
      map.removeControl(drawControl);
    };
  }, [map, setSelectedArea]);

  return null;
}

export function MapSelector() {
  return (
    <div className="w-full h-full" style={{ minHeight: '100%' }}>
      <MapContainer
        center={[50.4501, 30.5234]} // Київ
        zoom={13}
        style={{ height: "100%", width: "100%", minHeight: "100%" }}
        className="w-full h-full"
      >
        <TileLayer
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <DrawControl />
      </MapContainer>
    </div>
  );
}

