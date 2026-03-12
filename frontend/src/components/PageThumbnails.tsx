import { useEffect, useRef, useState, useCallback } from "react";
import type { Session } from "../types";
import { getPageImageUrl } from "../services/api";

interface Props {
  session: Session;
  currentPage: number;
  onSelectPage: (page: number) => void;
  pageVersions?: Record<number, number>;
}

const THUMB_WIDTH = 150;
const BUFFER = 3;

export default function PageThumbnails({
  session,
  currentPage,
  onSelectPage,
  pageVersions,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [visibleRange, setVisibleRange] = useState({ start: 1, end: 10 });

  const updateVisibleRange = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;

    const itemHeight = 180;
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
      className="h-full overflow-y-auto bg-gray-900 p-3 space-y-3"
    >
      {pages.map((pageNum) => {
        const isVisible =
          pageNum >= visibleRange.start && pageNum <= visibleRange.end;
        const isSelected = pageNum === currentPage;
        const version = pageVersions?.[pageNum];

        return (
          <button
            key={pageNum}
            onClick={() => onSelectPage(pageNum)}
            className={`
              block w-full rounded-lg overflow-hidden transition-all
              ${isSelected
                ? "ring-2 ring-blue-500 ring-offset-2 ring-offset-gray-900"
                : "hover:ring-2 hover:ring-gray-500 hover:ring-offset-1 hover:ring-offset-gray-900"
              }
            `}
          >
            {isVisible ? (
              <img
                src={getPageImageUrl(session.session_id, pageNum, version)}
                alt={`Page ${pageNum}`}
                width={THUMB_WIDTH}
                className="w-full h-auto bg-white"
                loading="lazy"
              />
            ) : (
              <div
                className="bg-gray-700 animate-pulse"
                style={{ width: THUMB_WIDTH, height: THUMB_WIDTH * 1.4 }}
              />
            )}
            <div
              className={`text-xs py-1 text-center ${
                isSelected ? "text-blue-400 font-semibold" : "text-gray-400"
              }`}
            >
              {pageNum}
            </div>
          </button>
        );
      })}
    </div>
  );
}
