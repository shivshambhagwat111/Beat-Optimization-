"use client";

import { Layers, Loader2, Sparkles } from "lucide-react";
import { useState } from "react";
import {
  ApiError,
  getBeats,
  getCombinedOptimizedBeats,
  triggerCombinedOptimize,
  triggerOptimize,
} from "@/lib/api";
import { useToast } from "./ToastProvider";

const POLL_INTERVAL_MS = 2500;
// ~5 minutes: each beat's map render does a road-routing call plus a TSP
// solve, done sequentially server-side, so more outlets/beats (especially in
// joint mode, which pools multiple distributors) can take a while.
const MAX_POLL_ATTEMPTS = 120;

type ActiveAction = "single" | "joint" | "perDistributor" | null;

interface OptimizePanelProps {
  distributorCode: string;
  onDistributorCodeChange: (value: string) => void;
  selectedCodes: string[];
  onOptimized: () => void;
  onJointOptimized: (codes: string[]) => void;
  onPerDistributorOptimized: (codes: string[]) => void;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function OptimizePanel({
  distributorCode,
  onDistributorCodeChange,
  selectedCodes,
  onOptimized,
  onJointOptimized,
  onPerDistributorOptimized,
}: OptimizePanelProps) {
  const [activeAction, setActiveAction] = useState<ActiveAction>(null);
  const { showToast } = useToast();

  const isJointMode = selectedCodes.length > 1;
  const isBusy = activeAction !== null;

  // The optimize endpoints only kick off a background task (202 Accepted) with
  // no completion signal, so we poll a getter until it stops 404-ing.
  const pollUntilReady = async (fetchOnce: () => Promise<unknown>): Promise<boolean> => {
    for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt++) {
      await delay(POLL_INTERVAL_MS);
      try {
        await fetchOnce();
        return true;
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          continue;
        }
        throw err;
      }
    }
    return false;
  };

  const handleOptimizeJoint = async () => {
    setActiveAction("joint");
    try {
      const result = await triggerCombinedOptimize(selectedCodes);
      showToast(
        "success",
        `${result.message} (${result.active_outlets} active outlets queued).`
      );

      const finished = await pollUntilReady(() => getCombinedOptimizedBeats(selectedCodes));
      if (finished) {
        showToast("success", "Jointly-optimized beats and routes are ready.");
        onJointOptimized(selectedCodes);
      } else {
        showToast("error", "Optimization is taking longer than expected. Try again shortly.");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to start optimization.";
      showToast("error", message);
    } finally {
      setActiveAction(null);
    }
  };

  // Runs a normal single-distributor optimize for every selected code, so the
  // "Optimized (Per-Distributor)" tab doesn't require typing each code into
  // the single-code box one at a time. Each optimize call only ever touches
  // that one distributor's beats, so firing them concurrently is safe.
  const handleOptimizePerDistributor = async () => {
    setActiveAction("perDistributor");
    try {
      const results = await Promise.all(selectedCodes.map((code) => triggerOptimize(code)));
      const totalActive = results.reduce((sum, r) => sum + r.active_outlets, 0);
      showToast(
        "success",
        `Optimization started for ${selectedCodes.length} distributors (${totalActive} active outlets queued).`
      );

      const finished = await pollUntilReady(() =>
        Promise.all(selectedCodes.map((code) => getBeats(code)))
      );
      if (finished) {
        showToast("success", "Per-distributor optimized beats are ready.");
        onPerDistributorOptimized(selectedCodes);
      } else {
        showToast("error", "Optimization is taking longer than expected. Try again shortly.");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to start optimization.";
      showToast("error", message);
    } finally {
      setActiveAction(null);
    }
  };

  const handleOptimizeSingle = async () => {
    const code = distributorCode.trim();
    if (!code) {
      showToast("error", "Please enter a distributor code.");
      return;
    }

    setActiveAction("single");
    try {
      const result = await triggerOptimize(code);
      showToast(
        "success",
        `${result.message} (${result.active_outlets} active outlets queued).`
      );

      const finished = await pollUntilReady(() => getBeats(code));
      if (finished) {
        showToast("success", "Optimized beats and routes are ready.");
        onOptimized();
      } else {
        showToast("error", "Optimization is taking longer than expected. Try again shortly.");
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Failed to start optimization.";
      showToast("error", message);
    } finally {
      setActiveAction(null);
    }
  };

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
      <div className="flex-1">
        <label
          htmlFor="distributor-code"
          className="mb-1 block text-sm font-medium text-slate-700"
        >
          Distributor Code
        </label>
        {isJointMode ? (
          <div className="w-full rounded-lg border border-slate-200 bg-slate-100 px-3 py-2 text-sm text-slate-500">
            {selectedCodes.length} distributors selected: {selectedCodes.join(", ")}
          </div>
        ) : (
          <input
            id="distributor-code"
            type="text"
            value={distributorCode}
            onChange={(e) => onDistributorCodeChange(e.target.value)}
            placeholder="e.g. D01"
            disabled={isBusy}
            className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm shadow-sm focus:border-amul-blue focus:outline-none focus:ring-1 focus:ring-amul-blue disabled:bg-slate-100"
          />
        )}
      </div>

      {isJointMode ? (
        <div className="flex gap-2">
          <button
            onClick={handleOptimizePerDistributor}
            disabled={isBusy}
            className="flex items-center justify-center gap-2 rounded-lg bg-slate-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {activeAction === "perDistributor" ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Optimizing…
              </>
            ) : (
              <>
                <Layers className="h-4 w-4" />
                Optimize {selectedCodes.length} distributors individually
              </>
            )}
          </button>
          <button
            onClick={handleOptimizeJoint}
            disabled={isBusy}
            className="flex items-center justify-center gap-2 rounded-lg bg-amul-blue px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-amul-blue-dark disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {activeAction === "joint" ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Optimizing…
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" />
                Optimize {selectedCodes.length} distributors combined
              </>
            )}
          </button>
        </div>
      ) : (
        <button
          onClick={handleOptimizeSingle}
          disabled={isBusy}
          className="flex items-center justify-center gap-2 rounded-lg bg-amul-blue px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-amul-blue-dark disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {activeAction === "single" ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              Optimizing…
            </>
          ) : (
            <>
              <Sparkles className="h-4 w-4" />
              Generate Optimized Beats
            </>
          )}
        </button>
      )}
    </div>
  );
}
