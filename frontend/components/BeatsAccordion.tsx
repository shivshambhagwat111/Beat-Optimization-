"use client";

import { ChevronDown, MapPin } from "lucide-react";
import { useState } from "react";
import type { BeatOut } from "@/lib/api";

interface BeatsAccordionProps {
  beats: BeatOut[];
  onOutletClick?: (retailerCode: string) => void;
}

export default function BeatsAccordion({ beats, onOutletClick }: BeatsAccordionProps) {
  const [openBeatId, setOpenBeatId] = useState<number | null>(beats[0]?.beat_id ?? null);

  return (
    <div className="flex flex-col gap-2">
      {beats.map((beat) => {
        const isOpen = openBeatId === beat.beat_id;
        return (
          <div
            key={beat.beat_id}
            className="overflow-hidden rounded-lg border border-slate-200"
          >
            <button
              onClick={() => setOpenBeatId(isOpen ? null : beat.beat_id)}
              className="flex w-full items-center justify-between bg-slate-50 px-4 py-3 text-left hover:bg-slate-100"
            >
              <div className="flex items-center gap-3">
                <span className="text-sm font-semibold text-slate-800">
                  {beat.beat_code}
                </span>
                <span className="rounded-full bg-amul-blue/10 px-2 py-0.5 text-xs font-medium text-amul-blue">
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
                      <th className="px-4 py-2 font-medium">Visit Order</th>
                      <th className="px-4 py-2 font-medium">Retailer Name</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {beat.outlets.map((outlet) => (
                      <tr
                        key={outlet.retailer_code}
                        onClick={() => onOutletClick?.(outlet.retailer_code)}
                        className="group cursor-pointer hover:bg-amul-blue/5"
                      >
                        <td className="px-4 py-2 font-medium text-amul-blue">
                          {outlet.visit_order}
                        </td>
                        <td className="px-4 py-2 text-slate-700">
                          <span className="flex items-center gap-1.5">
                            {outlet.name}
                            {outlet.distributor_code && (
                              <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-500">
                                {outlet.distributor_code}
                              </span>
                            )}
                            <MapPin className="h-3 w-3 shrink-0 text-slate-300 group-hover:text-amul-blue" />
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
