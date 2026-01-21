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
    const response = await axios.get(
      `${API_BASE_URL}/api/download/${taskId}${qs ? `?${qs}` : ""}`,
      { responseType: "blob" }
    );
    return response.data;
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

