"use client";

import Image from "next/image";
import { useCallback, useEffect, useRef, useState } from "react";
import BeatMapViewer, { type BeatMapViewerHandle } from "@/components/BeatMapViewer";
import BeatsAccordion from "@/components/BeatsAccordion";
import FileUploadZone from "@/components/FileUploadZone";
import OptimizePanel from "@/components/OptimizePanel";
import OriginalBeatsAccordion from "@/components/OriginalBeatsAccordion";
import { useToast } from "@/components/ToastProvider";
import {
  ApiError,
  getBeatMapUrl,
  getBeats,
  getCombinedBeatMapUrl,
  getCombinedBeats,
  getCombinedOptimizedBeatMapUrl,
  getCombinedOptimizedBeats,
  getCombinedOriginalBeatMapUrl,
  getCombinedOriginalBeats,
  getOriginalBeatMapUrl,
  getOriginalBeats,
  type BeatOut,
  type CombinedDistributorBeats,
  type CombinedDistributorOriginalBeats,
  type OriginalBeatOut,
} from "@/lib/api";

type ViewMode = "old" | "new" | "joint";

export default function DashboardPage() {
  const [distributorCode, setDistributorCode] = useState("");
  const [uploadedCodes, setUploadedCodes] = useState<string[]>([]);
  const [selectedCodes, setSelectedCodes] = useState<string[]>([]);
  const [viewMode, setViewMode] = useState<ViewMode>("old");

  // Single-distributor mode (selectedCodes.length === 1)
  const [activeDistributorName, setActiveDistributorName] = useState<string | null>(null);
  const [beats, setBeats] = useState<BeatOut[] | null>(null);
  const [isLoadingBeats, setIsLoadingBeats] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [originalBeats, setOriginalBeats] = useState<OriginalBeatOut[] | null>(null);
  const [isLoadingOriginalBeats, setIsLoadingOriginalBeats] = useState(false);
  const [originalRefreshKey, setOriginalRefreshKey] = useState(0);

  // Combined mode (selectedCodes.length >= 2)
  const [combinedBeats, setCombinedBeats] = useState<CombinedDistributorBeats[] | null>(null);
  const [isLoadingCombinedBeats, setIsLoadingCombinedBeats] = useState(false);
  const [combinedRefreshKey, setCombinedRefreshKey] = useState(0);
  const [combinedOriginalBeats, setCombinedOriginalBeats] = useState<
    CombinedDistributorOriginalBeats[] | null
  >(null);
  const [isLoadingCombinedOriginalBeats, setIsLoadingCombinedOriginalBeats] = useState(false);
  const [combinedOriginalRefreshKey, setCombinedOriginalRefreshKey] = useState(0);

  // Joint mode: 2+ distributors optimized together as one shared pool of beats.
  const [jointBeats, setJointBeats] = useState<BeatOut[] | null>(null);
  const [isLoadingJointBeats, setIsLoadingJointBeats] = useState(false);
  const [jointRefreshKey, setJointRefreshKey] = useState(0);

  const { showToast } = useToast();
  const mapViewerRef = useRef<BeatMapViewerHandle>(null);

  const handleOutletClick = useCallback((retailerCode: string) => {
    mapViewerRef.current?.focusOutlet(retailerCode);
  }, []);

  const loadBeats = useCallback(
    async (code: string) => {
      setIsLoadingBeats(true);
      try {
        const data = await getBeats(code);
        setBeats(data.beats);
        setActiveDistributorName(data.distributor_name);
        setRefreshKey((key) => key + 1);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) {
          const message = err instanceof Error ? err.message : "Failed to load beats.";
          showToast("error", message);
        }
        setBeats(null);
      } finally {
        setIsLoadingBeats(false);
      }
    },
    [showToast]
  );

  const loadOriginalBeats = useCallback(
    async (code: string) => {
      setIsLoadingOriginalBeats(true);
      try {
        const data = await getOriginalBeats(code);
        setOriginalBeats(data.beats);
        setActiveDistributorName(data.distributor_name);
        setOriginalRefreshKey((key) => key + 1);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) {
          const message = err instanceof Error ? err.message : "Failed to load original beats.";
          showToast("error", message);
        }
        setOriginalBeats(null);
      } finally {
        setIsLoadingOriginalBeats(false);
      }
    },
    [showToast]
  );

  const loadCombinedBeats = useCallback(
    async (codes: string[]) => {
      setIsLoadingCombinedBeats(true);
      try {
        const data = await getCombinedBeats(codes);
        setCombinedBeats(data.distributors);
        setCombinedRefreshKey((key) => key + 1);
      } catch (err) {
        const message = err instanceof Error ? err.message : "Failed to load combined beats.";
        showToast("error", message);
        setCombinedBeats(null);
      } finally {
        setIsLoadingCombinedBeats(false);
      }
    },
    [showToast]
  );

  const loadCombinedOriginalBeats = useCallback(
    async (codes: string[]) => {
      setIsLoadingCombinedOriginalBeats(true);
      try {
        const data = await getCombinedOriginalBeats(codes);
        setCombinedOriginalBeats(data.distributors);
        setCombinedOriginalRefreshKey((key) => key + 1);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : "Failed to load combined original beats.";
        showToast("error", message);
        setCombinedOriginalBeats(null);
      } finally {
        setIsLoadingCombinedOriginalBeats(false);
      }
    },
    [showToast]
  );

  const loadJointBeats = useCallback(
    async (codes: string[]) => {
      setIsLoadingJointBeats(true);
      try {
        const data = await getCombinedOptimizedBeats(codes);
        setJointBeats(data.beats);
        setJointRefreshKey((key) => key + 1);
      } catch (err) {
        if (!(err instanceof ApiError && err.status === 404)) {
          const message = err instanceof Error ? err.message : "Failed to load jointly-optimized beats.";
          showToast("error", message);
        }
        setJointBeats(null);
      } finally {
        setIsLoadingJointBeats(false);
      }
    },
    [showToast]
  );

  // Single place that reacts to "what's selected" and "which view" — covers
  // single-distributor selection, multi-distributor (combined) selection, and
  // switching between Old/New for either, without each UI action needing to
  // know how to trigger the right fetch itself.
  useEffect(() => {
    if (selectedCodes.length === 0) return;
    if (selectedCodes.length === 1) {
      if (viewMode === "new") loadBeats(selectedCodes[0]);
      else if (viewMode === "old") loadOriginalBeats(selectedCodes[0]);
      // "joint" is not reachable with a single selected code — the guard
      // effect below resets it to "new" as soon as the selection drops to 1.
    } else {
      if (viewMode === "new") loadCombinedBeats(selectedCodes);
      else if (viewMode === "old") loadCombinedOriginalBeats(selectedCodes);
      else loadJointBeats(selectedCodes);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedCodes, viewMode]);

  // The "Jointly Optimized" toggle only makes sense with 2+ distributors
  // selected — fall back to "new" if the selection drops below that.
  useEffect(() => {
    if (viewMode === "joint" && selectedCodes.length <= 1) {
      setViewMode("new");
    }
  }, [viewMode, selectedCodes]);

  const handleToggleDistributor = useCallback((code: string) => {
    setSelectedCodes((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
  }, []);

  const handleUploaded = useCallback((codes: string[]) => {
    setUploadedCodes(codes);
    if (codes.length === 1) {
      setDistributorCode(codes[0]);
      setSelectedCodes([codes[0]]);
      setViewMode("old");
    }
  }, []);

  const handleOptimized = useCallback(() => {
    const code = distributorCode.trim();
    if (!code) return;
    setSelectedCodes([code]);
    setViewMode("new");
  }, [distributorCode]);

  const handleJointOptimized = useCallback((codes: string[]) => {
    setSelectedCodes(codes);
    setViewMode("joint");
  }, []);

  const handlePerDistributorOptimized = useCallback((codes: string[]) => {
    setSelectedCodes(codes);
    setViewMode("new");
  }, []);

  const isCombined = selectedCodes.length > 1;
  const isLoadingActive = isCombined
    ? viewMode === "new"
      ? isLoadingCombinedBeats
      : viewMode === "old"
        ? isLoadingCombinedOriginalBeats
        : isLoadingJointBeats
    : viewMode === "new"
      ? isLoadingBeats
      : isLoadingOriginalBeats;

  const hasData = isCombined
    ? viewMode === "new"
      ? !!combinedBeats && combinedBeats.length > 0
      : viewMode === "old"
        ? !!combinedOriginalBeats && combinedOriginalBeats.length > 0
        : !!jointBeats && jointBeats.length > 0
    : viewMode === "new"
      ? !!beats && beats.length > 0
      : !!originalBeats && originalBeats.length > 0;

  const mapUrl =
    selectedCodes.length === 0
      ? null
      : isCombined
        ? viewMode === "new"
          ? getCombinedBeatMapUrl(selectedCodes)
          : viewMode === "old"
            ? getCombinedOriginalBeatMapUrl(selectedCodes)
            : getCombinedOptimizedBeatMapUrl(selectedCodes)
        : viewMode === "new"
          ? getBeatMapUrl(selectedCodes[0])
          : getOriginalBeatMapUrl(selectedCodes[0]);

  const activeRefreshKey = isCombined
    ? viewMode === "new"
      ? combinedRefreshKey
      : viewMode === "old"
        ? combinedOriginalRefreshKey
        : jointRefreshKey
    : viewMode === "new"
      ? refreshKey
      : originalRefreshKey;

  return (
    <main className="min-h-screen bg-slate-100 pb-16">
      <div className="h-1.5 bg-amul-red" />
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center gap-4 px-6 py-6">
          <Image
            src="/amul-logo.png"
            alt="Amul"
            width={90}
            height={72}
            className="h-14 w-auto shrink-0 rounded-md shadow-sm"
            priority
          />
          <div className="h-10 w-px bg-slate-200" />
          <div>
            <h1 className="text-2xl font-bold text-slate-900">Sales Beat Optimizer</h1>
            <p className="mt-1 text-sm text-slate-500">
              Upload your retail master sheet, generate capacity-balanced sales beats, and
              review optimized routes on the map.
            </p>
          </div>
        </div>
      </header>

      <div className="mx-auto flex max-w-7xl flex-col gap-6 px-6 py-8">
        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
            1. Upload Retail Master
          </h2>
          <FileUploadZone onUploaded={handleUploaded} />

          {uploadedCodes.length > 1 && (
            <div className="mt-4">
              <p className="mb-2 text-xs font-medium text-slate-500">
                Found {uploadedCodes.length} distributor codes in this file — select one to
                view, or select 2+ to compare them together on the map:
              </p>
              <div className="flex flex-wrap gap-2">
                {uploadedCodes.map((code) => {
                  const isSelected = selectedCodes.includes(code);
                  return (
                    <button
                      key={code}
                      onClick={() => handleToggleDistributor(code)}
                      className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                        isSelected
                          ? "border-amul-blue bg-amul-blue text-white"
                          : "border-slate-300 text-slate-600 hover:border-amul-blue hover:text-amul-blue"
                      }`}
                    >
                      {code}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </section>

        <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
            2. Generate Optimized Beats
          </h2>
          <OptimizePanel
            distributorCode={distributorCode}
            onDistributorCodeChange={setDistributorCode}
            selectedCodes={selectedCodes}
            onOptimized={handleOptimized}
            onJointOptimized={handleJointOptimized}
            onPerDistributorOptimized={handlePerDistributorOptimized}
          />
        </section>

        {selectedCodes.length > 0 && (
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-sm text-slate-500">
              {isCombined ? (
                <>
                  Comparing{" "}
                  <span className="font-semibold text-slate-800">
                    {selectedCodes.length} distributors
                  </span>
                  : <span className="text-slate-400">{selectedCodes.join(", ")}</span>
                </>
              ) : (
                <>
                  Showing results for{" "}
                  <span className="font-semibold text-slate-800">
                    {activeDistributorName ?? selectedCodes[0]}
                  </span>{" "}
                  <span className="text-slate-400">({selectedCodes[0]})</span>
                </>
              )}
            </p>

            <div className="inline-flex self-start rounded-lg border border-slate-200 bg-white p-1 shadow-sm">
              <button
                onClick={() => setViewMode("old")}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  viewMode === "old"
                    ? "bg-amul-red text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                Original (Old)
              </button>
              <button
                onClick={() => setViewMode("new")}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                  viewMode === "new"
                    ? "bg-amul-blue text-white"
                    : "text-slate-600 hover:bg-slate-100"
                }`}
              >
                {isCombined ? "Optimized (Per-Distributor)" : "Optimized (New)"}
              </button>
              {isCombined && (
                <button
                  onClick={() => setViewMode("joint")}
                  className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
                    viewMode === "joint"
                      ? "bg-emerald-600 text-white"
                      : "text-slate-600 hover:bg-slate-100"
                  }`}
                >
                  Jointly Optimized
                </button>
              )}
            </div>
          </div>
        )}

        <section className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
              Route Map{" "}
              {viewMode === "old" ? "— Original" : viewMode === "joint" ? "— Jointly Optimized" : "— Optimized"}
              {isCombined ? (viewMode === "new" ? " (Per-Distributor)" : " (Combined)") : ""}
            </h2>
            <BeatMapViewer
              ref={mapViewerRef}
              mapUrl={mapUrl}
              refreshKey={activeRefreshKey}
              viewMode={viewMode}
              hasData={hasData}
              title={`${viewMode === "old" ? "Original" : viewMode === "joint" ? "Jointly optimized" : "Optimized"} beat map for ${selectedCodes.join(", ") || "—"}`}
            />
          </div>

          <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="mb-4 text-sm font-semibold uppercase tracking-wide text-slate-400">
              {viewMode === "old" ? "Original Beats" : viewMode === "joint" ? "Jointly Optimized Beats & Visit Sequence" : "Beats & Visit Sequence"}
              {isCombined ? (viewMode === "new" ? " (Per-Distributor)" : viewMode === "old" ? " (Combined)" : "") : ""}
            </h2>

            {isLoadingActive ? (
              <div className="flex flex-col gap-2">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="h-12 animate-pulse rounded-lg bg-slate-100" />
                ))}
              </div>
            ) : hasData ? (
              isCombined ? (
                viewMode === "joint" ? (
                  <BeatsAccordion beats={jointBeats ?? []} onOutletClick={handleOutletClick} />
                ) : (
                <div className="flex flex-col gap-5">
                  {viewMode === "new"
                    ? combinedBeats?.map((distributor) => (
                        <div key={distributor.distributor_code}>
                          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-amul-blue">
                            {distributor.distributor_name} ({distributor.distributor_code})
                          </p>
                          <BeatsAccordion
                            beats={distributor.beats}
                            onOutletClick={handleOutletClick}
                          />
                        </div>
                      ))
                    : combinedOriginalBeats?.map((distributor) => (
                        <div key={distributor.distributor_code}>
                          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-amul-red">
                            {distributor.distributor_name} ({distributor.distributor_code})
                          </p>
                          <OriginalBeatsAccordion
                            beats={distributor.beats}
                            onOutletClick={handleOutletClick}
                          />
                        </div>
                      ))}
                </div>
                )
              ) : viewMode === "new" ? (
                <BeatsAccordion beats={beats ?? []} onOutletClick={handleOutletClick} />
              ) : (
                <OriginalBeatsAccordion
                  beats={originalBeats ?? []}
                  onOutletClick={handleOutletClick}
                />
              )
            ) : (
              <div className="flex h-full min-h-[300px] flex-col items-center justify-center gap-2 text-center">
                <p className="text-sm font-medium text-slate-500">
                  {viewMode === "new"
                    ? "No beats generated yet"
                    : viewMode === "joint"
                      ? "No jointly-optimized beats yet"
                      : "No original beat data"}
                </p>
                <p className="max-w-xs text-xs text-slate-400">
                  {viewMode === "new"
                    ? "Run the optimizer above to see grouped beats and visit order here."
                    : viewMode === "joint"
                      ? "Select 2+ distributors above and click \"Optimize N distributors combined\" to pool their outlets into new shared beats."
                      : 'Upload a master sheet with a "Sales Route" column to see the pre-existing beat grouping here.'}
                </p>
              </div>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
