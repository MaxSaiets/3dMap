import axios from "axios";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface GenerationRequest {
  north: number;
  south: number;
  east: number;
  west: number;
  road_width_multiplier: number;
  road_height_mm: number;
  road_embed_mm: number;
  building_min_height: number;
  building_height_multiplier: number;
  building_foundation_mm: number;
  building_embed_mm: number;
  water_depth: number;
  terrain_enabled: boolean;
  terrain_z_scale: number;
  terrain_base_thickness_mm: number;
  terrain_resolution: number;
  terrarium_zoom: number;
  flatten_buildings_on_terrain?: boolean;
  export_format: "stl" | "3mf";
  model_size_mm: number;
}

export interface GenerationResponse {
  task_id: string;
  status: string;
}

export interface TaskStatus {
  task_id: string;
  status: string;
  progress: number;
  message: string;
  download_url: string | null;
  download_url_stl?: string | null;
  download_url_3mf?: string | null;
  preview_parts?: {
    base?: string | null;
    roads?: string | null;
    buildings?: string | null;
    water?: string | null;
    parks?: string | null;
    poi?: string | null;
  };
  output_files?: Record<string, string>;
}

export interface BatchTaskStatusResponse {
  task_id: string;
  status: "multiple";
  tasks: TaskStatus[];
  total: number;
  completed: number;
  all_task_ids: string[];
}

export type StatusResponse = TaskStatus | BatchTaskStatusResponse;

export const api = {
  async generateModel(request: GenerationRequest): Promise<GenerationResponse> {
    const response = await axios.post<GenerationResponse>(
      `${API_BASE_URL}/api/generate`,
      request
    );
    return response.data;
  },

  async getStatus(taskId: string): Promise<StatusResponse> {
    const response = await axios.get<StatusResponse>(
      `${API_BASE_URL}/api/status/${taskId}`
    );
    return response.data;
  },

  async downloadModel(
    taskId: string,
    format?: "stl" | "3mf",
    part?: "base" | "roads" | "buildings" | "water" | "parks" | "poi"
  ): Promise<Blob> {
    const params = new URLSearchParams();
    if (format) params.set("format", format);
    if (part) params.set("part", part);
    const qs = params.toString();
    const url = `${API_BASE_URL}/api/download/${taskId}${qs ? `?${qs}` : ""}`;

    const CHUNK_SIZE = 1024 * 1024; // 1MB chunks as requested

    try {
      // 1. Get file size first via HEAD to handle redirects and get headers
      const probe = await fetch(url, { method: 'HEAD' });

      // If HEAD fails or gives incomplete info, we might need a GET probe
      let totalSize = Number(probe.headers.get("Content-Length"));

      if (!totalSize || isNaN(totalSize) || totalSize === 0) {
        // Fallback: try GET range 0-0
        try {
          const probe2 = await fetch(url, { headers: { Range: "bytes=0-0" } });
          const rangeHeader = probe2.headers.get("Content-Range"); // bytes 0-0/12345
          if (rangeHeader) {
            const match = rangeHeader.match(/\/(\d+)$/);
            if (match) totalSize = Number(match[1]);
          }
        } catch (e) { /* ignore */ }
      }

      // If still unknown (or small < 5MB), just classic download
      if (!totalSize || totalSize < 5 * CHUNK_SIZE) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`Status ${res.status}`);
        return await res.blob();
      }

      // 2. Download in chunks
      console.log(`[Download] Starting chunked download: ${totalSize} bytes in ${(totalSize / CHUNK_SIZE).toFixed(1)} chunks`);
      const chunks: Blob[] = [];
      let loaded = 0;

      while (loaded < totalSize) {
        const end = Math.min(loaded + CHUNK_SIZE - 1, totalSize - 1);
        const range = `bytes=${loaded}-${end}`;

        let chunkSuccess = false;
        let chunkAttempts = 0;

        while (!chunkSuccess && chunkAttempts < 5) {
          try {
            // IMPORTANT: Fetch usually follows redirects.
            // The redirected URL (StaticFile) respects Range.
            const chunkRes = await fetch(url, {
              headers: { Range: range }
            });

            if (!chunkRes.ok && chunkRes.status !== 206) {
              // If 200 OK returned on Range request, it ignored range -> we got full file.
              // Just return it (if first chunk) or handle error.
              if (loaded === 0 && chunkRes.status === 200) {
                return await chunkRes.blob();
              }
              throw new Error(`Chunk status: ${chunkRes.status}`);
            }

            const blob = await chunkRes.blob();
            chunks.push(blob);
            loaded += blob.size;
            chunkSuccess = true;
          } catch (e) {
            chunkAttempts++;
            console.warn(`[Download] Chunk retry ${chunkAttempts}:`, e);
            await new Promise(r => setTimeout(r, 1000));
          }
        }
        if (!chunkSuccess) throw new Error("Failed to download chunk after retries");
      }

      return new Blob(chunks);

    } catch (err) {
      console.error("Chunked download critical fail:", err);
      // Last resort: simple fetch
      const finalTry = await fetch(url);
      if (!finalTry.ok) throw err;
      return await finalTry.blob();
    }
  },

  async generateHexagonalGrid(bounds: {
    north: number;
    south: number;
    east: number;
    west: number;
    hex_size_m?: number;
    grid_type?: "hexagonal" | "square";
  }): Promise<{
    geojson: any;
    hex_count: number;
    is_valid: boolean;
    validation_errors: string[];
  }> {
    const response = await axios.post(
      `${API_BASE_URL}/api/hexagonal-grid`,
      {
        ...bounds,
        hex_size_m: bounds.hex_size_m || 400.0,
        grid_type: bounds.grid_type || "hexagonal",
      }
    );
    return response.data;
  },

  async generateZones(
    zones: any[],
    params: GenerationRequest
  ): Promise<GenerationResponse & { all_task_ids?: string[] }> {
    const response = await axios.post(
      `${API_BASE_URL}/api/generate-zones`,
      {
        zones,
        ...params,
      }
    );
    return response.data;
  },
};

