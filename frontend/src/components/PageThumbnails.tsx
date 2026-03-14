import { useEffect, useRef, useState, useCallback } from "react";
import type { Session, PageEditType } from "@/types";
import { getPageImageUrl } from "@/services/api";
import { cn } from "@/lib/utils";

interface Props {
  session: Session;
  currentPage: number;
  onSelectPage: (page: number) => void;
  pageVersions?: Record<number, number>;
  pageEditTypes?: Record<number, PageEditType>;
}

const BUFFER = 3;

function EditTypeIndicator({ editType }: { editType: PageEditType }) {
  const hasProgram = editType.hasProgram;
  const hasVisual = editType.hasVisual;
  if (!hasProgram && !hasVisual) return null;

  let label: string;
  let bgClass: string;
  if (hasProgram && hasVisual) {
    label = "M";
    bgClass = "bg-purple-500";
  } else if (hasProgram) {
    label = "P";
    bgClass = "bg-green-500";
  } else {
    label = "V";
    bgClass = "bg-blue-500";
  }

  return (
    <div
      className={cn(
        "absolute top-0.5 right-0.5 z-10 h-3.5 w-3.5 rounded-full text-[8px] font-bold text-white",
        "flex items-center justify-center shadow-sm",
        bgClass,
      )}
      title={
        hasProgram && hasVisual
          ? "Programmatic + AI edits"
          : hasProgram
            ? "Programmatic edit"
            : "AI edit"
      }
    >
      {label}
    </div>
  );
}

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

    const itemHeight = 80;
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
      className="h-full overflow-y-auto p-1.5 space-y-1.5"
    >
      {pages.map((pageNum) => {
        const isVisible =
          pageNum >= visibleRange.start && pageNum <= visibleRange.end;
        const isSelected = pageNum === currentPage;
        const version = pageVersions?.[pageNum];
        const isEdited = version !== undefined && version > 0;
        const editType = pageEditTypes?.[pageNum];

        return (
          <button
            key={pageNum}
            onClick={() => onSelectPage(pageNum)}
            className={cn(
              "relative block w-full rounded overflow-hidden transition-all",
              isSelected
                ? "ring-2 ring-blue-500 ring-offset-1 ring-offset-background"
                : "hover:ring-1 hover:ring-muted-foreground/40",
            )}
          >
            {isEdited && editType && <EditTypeIndicator editType={editType} />}
            {isVisible ? (
              <img
                src={getPageImageUrl(session.session_id, pageNum, version)}
                alt={`Page ${pageNum}`}
                className="w-full h-auto bg-white"
                loading="lazy"
              />
            ) : (
              <div className="w-full aspect-[1/1.4] bg-muted animate-pulse rounded" />
            )}
            <div
              className={cn(
                "text-[9px] py-0.5 text-center",
                isSelected
                  ? "text-blue-500 font-semibold"
                  : "text-muted-foreground",
              )}
            >
              {pageNum}
            </div>
          </button>
        );
      })}
    </div>
  );
}
