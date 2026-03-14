import { useEffect, useRef, useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import type { Session, PageEditType } from "@/types";
import { getPageImageUrl } from "@/services/api";

interface Props {
  session: Session;
  currentPage: number;
  onSelectPage: (page: number) => void;
  pageVersions?: Record<number, number>;
  pageEditTypes?: Record<number, PageEditType>;
}

const BUFFER = 3;

export default function PageThumbnails({
  session,
  currentPage,
  onSelectPage,
  pageVersions,
  pageEditTypes,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visibleRange, setVisibleRange] = useState({ start: 1, end: 20 });

  const updateVisibleRange = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const itemHeight = 76;
    const scrollTop = el.scrollTop;
    const viewHeight = el.clientHeight;
    const start = Math.max(1, Math.floor(scrollTop / itemHeight) - BUFFER + 1);
    const end = Math.min(
      session.page_count,
      Math.ceil((scrollTop + viewHeight) / itemHeight) + BUFFER,
    );
    setVisibleRange({ start, end });
  }, [session.page_count]);

  useEffect(() => {
    updateVisibleRange();
  }, [updateVisibleRange]);

  const pages = Array.from({ length: session.page_count }, (_, i) => i + 1);

  return (
    <div
      ref={containerRef}
      onScroll={updateVisibleRange}
      className="h-full overflow-y-auto py-2"
    >
      {pages.map((pageNum) => {
        const isVisible = pageNum >= visibleRange.start && pageNum <= visibleRange.end;
        const isSelected = pageNum === currentPage;
        const version = pageVersions?.[pageNum];
        const hasEdit = version !== undefined && version > 0;
        const editType = pageEditTypes?.[pageNum];

        return (
          <button
            key={pageNum}
            onClick={() => onSelectPage(pageNum)}
            className={cn(
              "relative mx-auto mb-1 block w-[48px] transition-all",
              isSelected && "scale-105",
            )}
          >
            {/* Selection indicator — left bar */}
            <div
              className={cn(
                "absolute -left-[5px] top-1 bottom-4 w-[3px] rounded-r-full transition-colors",
                isSelected ? "bg-blue-500" : "bg-transparent",
              )}
            />

            {/* Thumbnail image */}
            <div
              className={cn(
                "overflow-hidden rounded-[3px] border transition-all",
                isSelected
                  ? "border-blue-500/60 shadow-sm shadow-blue-500/20"
                  : "border-transparent hover:border-border",
              )}
            >
              {isVisible ? (
                <img
                  src={getPageImageUrl(session.session_id, pageNum, version)}
                  alt={`Page ${pageNum}`}
                  className="w-full h-auto bg-white"
                  loading="lazy"
                />
              ) : (
                <div className="aspect-[1/1.4] w-full animate-pulse bg-muted" />
              )}
            </div>

            {/* Page number + edit dot */}
            <div className="mt-0.5 flex items-center justify-center gap-1">
              {hasEdit && (
                <div
                  className={cn(
                    "h-1.5 w-1.5 rounded-full",
                    editType?.hasProgram && editType?.hasVisual
                      ? "bg-purple-500"
                      : editType?.hasProgram
                        ? "bg-green-500"
                        : "bg-blue-500",
                  )}
                />
              )}
              <span
                className={cn(
                  "text-[10px] tabular-nums",
                  isSelected ? "text-blue-500 font-medium" : "text-muted-foreground",
                )}
              >
                {pageNum}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}
