import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import { API_BASE_URL } from "@/lib/api";

interface BeatMapViewerProps {
  mapUrl: string | null;
  refreshKey: number;
  viewMode: "old" | "new" | "joint";
  hasData: boolean;
  title: string;
}

export interface BeatMapViewerHandle {
  focusOutlet: (retailerCode: string) => void;
}

const BeatMapViewer = forwardRef<BeatMapViewerHandle, BeatMapViewerProps>(function BeatMapViewer(
  { mapUrl, refreshKey, viewMode, hasData, title },
  ref
) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const isLoadedRef = useRef(false);
  const pendingCodeRef = useRef<string | null>(null);

  const sendFocus = (retailerCode: string) => {
    iframeRef.current?.contentWindow?.postMessage(
      { type: "focusOutlet", retailerCode },
      API_BASE_URL
    );
  };

  useImperativeHandle(ref, () => ({
    focusOutlet: (retailerCode: string) => {
      // The map iframe's own script (which registers the postMessage listener)
      // may not have finished loading yet — a message sent before that happens
      // is silently dropped, not queued. Wait for load, then deliver it.
      if (isLoadedRef.current) {
        sendFocus(retailerCode);
      } else {
        pendingCodeRef.current = retailerCode;
      }
    },
  }));

  const src = mapUrl ? `${mapUrl}?v=${refreshKey}` : null;

  // Reset the "loaded" flag whenever the iframe is about to point at a new
  // document (new distributor, refreshed map, or switched view mode).
  useEffect(() => {
    isLoadedRef.current = false;
    pendingCodeRef.current = null;
  }, [src]);

  if (!hasData || !src) {
    return (
      <div className="flex h-full min-h-[500px] flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-slate-300 bg-slate-50 text-center">
        <p className="text-sm font-medium text-slate-500">No map to display yet</p>
        <p className="max-w-xs text-xs text-slate-400">
          {viewMode === "new"
            ? "Upload outlets and generate optimized beats to see the route map here."
            : viewMode === "joint"
              ? "Select 2+ distributors and run a combined optimize to see the shared route map here."
              : 'Upload a master sheet with a "Sales Route" column to see the original beat map here.'}
        </p>
      </div>
    );
  }

  return (
    <iframe
      key={src}
      ref={iframeRef}
      src={src}
      onLoad={() => {
        isLoadedRef.current = true;
        if (pendingCodeRef.current) {
          sendFocus(pendingCodeRef.current);
          pendingCodeRef.current = null;
        }
      }}
      title={title}
      className="h-full min-h-[500px] w-full rounded-xl border border-slate-200 bg-white"
    />
  );
});

export default BeatMapViewer;
