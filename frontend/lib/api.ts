export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export interface UploadResponse {
  message: string;
  distributor_codes: string[];
  total_outlets: number;
  inserted: number;
  updated: number;
  duplicates_removed: number;
}

export interface OptimizeResponse {
  message: string;
  active_outlets: number;
}

export interface OutletOut {
  retailer_code: string;
  name: string;
  latitude: number;
  longitude: number;
  visit_order: number;
  // Only populated on the jointly-optimized combined response.
  distributor_code?: string;
  distributor_name?: string;
}

export interface BeatOut {
  beat_id: number;
  beat_code: string;
  total_outlets: number;
  outlets: OutletOut[];
}

export interface BeatsResponse {
  distributor_code: string;
  distributor_name: string;
  beats: BeatOut[];
}

export interface DistributorOut {
  code: string;
  name: string;
}

export interface OriginalOutletOut {
  retailer_code: string;
  name: string;
  latitude: number;
  longitude: number;
}

export interface OriginalBeatOut {
  beat_name: string;
  total_outlets: number;
  outlets: OriginalOutletOut[];
}

export interface OriginalBeatsResponse {
  distributor_code: string;
  distributor_name: string;
  beats: OriginalBeatOut[];
}

export interface CombinedDistributorBeats {
  distributor_code: string;
  distributor_name: string;
  beats: BeatOut[];
}

export interface CombinedBeatsResponse {
  distributor_codes: string[];
  distributors: CombinedDistributorBeats[];
}

export interface CombinedDistributorOriginalBeats {
  distributor_code: string;
  distributor_name: string;
  beats: OriginalBeatOut[];
}

export interface CombinedOriginalBeatsResponse {
  distributor_codes: string[];
  distributors: CombinedDistributorOriginalBeats[];
}

export interface CombinedOptimizedBeatsResponse {
  distributor_codes: string[];
  beats: BeatOut[];
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function parseErrorDetail(res: Response): Promise<string> {
  try {
    const data = await res.json();
    return typeof data?.detail === "string" ? data.detail : res.statusText;
  } catch {
    return res.statusText || `Request failed with status ${res.status}`;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE_URL}${path}`, init);
  if (!res.ok) {
    throw new ApiError(await parseErrorDetail(res), res.status);
  }
  return res.json() as Promise<T>;
}

export function uploadOutlets(file: File): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append("file", file);

  return request<UploadResponse>("/api/upload", {
    method: "POST",
    body: formData,
  });
}

export function triggerOptimize(distributorCode: string): Promise<OptimizeResponse> {
  return request<OptimizeResponse>(
    `/api/optimize/${encodeURIComponent(distributorCode)}`,
    { method: "POST" }
  );
}

export function triggerCombinedOptimize(distributorCodes: string[]): Promise<OptimizeResponse> {
  return request<OptimizeResponse>(
    `/api/optimize/combined?codes=${encodeURIComponent(sortedCodesParam(distributorCodes))}`,
    { method: "POST" }
  );
}

export function getBeats(distributorCode: string): Promise<BeatsResponse> {
  return request<BeatsResponse>(`/api/beats/${encodeURIComponent(distributorCode)}`, {
    cache: "no-store",
  });
}

export function getBeatMapUrl(distributorCode: string): string {
  return `${API_BASE_URL}/output/beats_map_${encodeURIComponent(distributorCode)}.html`;
}

export function getDistributor(distributorCode: string): Promise<DistributorOut> {
  return request<DistributorOut>(`/api/distributors/${encodeURIComponent(distributorCode)}`, {
    cache: "no-store",
  });
}

export function updateDistributorName(
  distributorCode: string,
  name: string
): Promise<DistributorOut> {
  return request<DistributorOut>(`/api/distributors/${encodeURIComponent(distributorCode)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function getOriginalBeats(distributorCode: string): Promise<OriginalBeatsResponse> {
  return request<OriginalBeatsResponse>(
    `/api/original-beats/${encodeURIComponent(distributorCode)}`,
    { cache: "no-store" }
  );
}

export function getOriginalBeatMapUrl(distributorCode: string): string {
  return `${API_BASE_URL}/output/original_beats_map_${encodeURIComponent(distributorCode)}.html`;
}

function sortedCodesParam(distributorCodes: string[]): string {
  return [...distributorCodes].sort().join(",");
}

export function getCombinedBeats(distributorCodes: string[]): Promise<CombinedBeatsResponse> {
  return request<CombinedBeatsResponse>(
    `/api/beats/combined?codes=${encodeURIComponent(sortedCodesParam(distributorCodes))}`,
    { cache: "no-store" }
  );
}

export function getCombinedOriginalBeats(
  distributorCodes: string[]
): Promise<CombinedOriginalBeatsResponse> {
  return request<CombinedOriginalBeatsResponse>(
    `/api/original-beats/combined?codes=${encodeURIComponent(sortedCodesParam(distributorCodes))}`,
    { cache: "no-store" }
  );
}

// These must match the backend's `_combined_map_filename` naming exactly:
// sorted codes joined with "_", since the file is generated deterministically
// from that same sorted list.
export function getCombinedBeatMapUrl(distributorCodes: string[]): string {
  const slug = [...distributorCodes].sort().join("_");
  return `${API_BASE_URL}/output/combined_beats_map_${slug}.html`;
}

export function getCombinedOriginalBeatMapUrl(distributorCodes: string[]): string {
  const slug = [...distributorCodes].sort().join("_");
  return `${API_BASE_URL}/output/combined_original_beats_map_${slug}.html`;
}

export function getCombinedOptimizedBeats(
  distributorCodes: string[]
): Promise<CombinedOptimizedBeatsResponse> {
  return request<CombinedOptimizedBeatsResponse>(
    `/api/beats/combined-optimized?codes=${encodeURIComponent(sortedCodesParam(distributorCodes))}`,
    { cache: "no-store" }
  );
}

export function getCombinedOptimizedBeatMapUrl(distributorCodes: string[]): string {
  const slug = [...distributorCodes].sort().join("_");
  return `${API_BASE_URL}/output/combined_optimized_beats_map_${slug}.html`;
}
