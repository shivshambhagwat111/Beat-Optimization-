"use client";

import { ChevronDown, MapPin } from "lucide-react";
import { useState } from "react";
import type { OriginalBeatOut } from "@/lib/api";

interface OriginalBeatsAccordionProps {
  beats: OriginalBeatOut[];
  onOutletClick?: (retailerCode: string) => void;
}

export default function OriginalBeatsAccordion({
  beats,
  onOutletClick,
}: OriginalBeatsAccordionProps) {
  const [openBeatName, setOpenBeatName] = useState<string | null>(beats[0]?.beat_name ?? null);

  return (
    <div className="flex flex-col gap-2">
      {beats.map((beat) => {
        const isOpen = openBeatName === beat.beat_name;
        return (
          <div
            key={beat.beat_name}
            className="overflow-hidden rounded-lg border border-slate-200"
          >
            <button
              onClick={() => setOpenBeatName(isOpen ? null : beat.beat_name)}
              className="flex w-full items-center justify-between bg-slate-50 px-4 py-3 text-left hover:bg-slate-100"
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-slate-800">
                  {beat.beat_name}
                </span>
                <span className="rounded-full bg-amul-red/10 px-2 py-0.5 text-xs font-medium text-amul-red">
                  {beat.total_outlets} outlets
                </span>
              </div>
              <ChevronDown
                className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${
                  isOpen ? "rotate-180" : ""
                }`}
              />
            </button>

            {isOpen && (
              <div className="max-h-72 overflow-y-auto border-t border-slate-200">
                <table className="w-full text-left text-sm">
                  <thead className="sticky top-0 bg-white text-xs uppercase tracking-wide text-slate-400">
                    <tr>
                      <th className="px-4 py-2 font-medium">Retailer Name</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {beat.outlets.map((outlet) => (
                      <tr
                        key={outlet.retailer_code}
                        onClick={() => onOutletClick?.(outlet.retailer_code)}
                        className="group cursor-pointer hover:bg-amul-red/5"
                      >
                        <td className="px-4 py-2 text-slate-700">
                          <span className="flex items-center gap-1.5">
                            {outlet.name}
                            <MapPin className="h-3 w-3 shrink-0 text-slate-300 group-hover:text-amul-red" />
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
