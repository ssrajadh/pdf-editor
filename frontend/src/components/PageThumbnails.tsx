import { useEffect, useRef } from "react";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
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

/** Single thumbnail with IntersectionObserver-based lazy loading. */
function Thumbnail({
  sessionId,
  pageNum,
  version,
  isSelected,
  editType,
  hasEdit,
  onSelect,
}: {
  sessionId: string;
  pageNum: number;
  version?: number;
  isSelected: boolean;
  editType?: PageEditType;
  hasEdit: boolean;
  onSelect: () => void;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const sentinelRef = useRef<HTMLDivElement>(null);

  // Lazy-load via IntersectionObserver
  useEffect(() => {
    const sentinel = sentinelRef.current;
    const img = imgRef.current;
    if (!sentinel || !img) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          const url = getPageImageUrl(sessionId, pageNum, version);
          img.src = url;
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [sessionId, pageNum, version]);

  const showProg = hasEdit && editType?.hasProgram;
  const showVis = hasEdit && editType?.hasVisual;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          onClick={onSelect}
          className={cn(
            "group relative mx-auto mb-2 block w-[108px] rounded-sm transition-colors overflow-hidden",
            !isSelected && "hover:bg-muted",
          )}
        >
          {/* Selection ring */}
          <div
            className={cn(
              "overflow-hidden rounded-[3px] border-2 transition-all",
              isSelected
                ? "border-primary ring-1 ring-primary/30"
                : "border-transparent",
            )}
          >
            {/* Sentinel for IntersectionObserver + skeleton */}
            <div ref={sentinelRef} className="aspect-[1/1.41] w-full bg-muted">
              <img
                ref={imgRef}
                alt={`Page ${pageNum}`}
                className="h-full w-full object-contain bg-white"
              />
            </div>
          </div>

          {/* Edit indicator dots */}
          {(showProg || showVis) && (
            <div className="absolute top-1 right-1 flex gap-[2px]">
              {showProg && (
                <div className="h-2 w-2 rounded-full border border-background bg-blue-500" />
              )}
              {showVis && (
                <div className="h-2 w-2 rounded-full border border-background bg-purple-500" />
              )}
            </div>
          )}

          {/* Page number */}
          <span
            className={cn(
              "mt-1 block text-center text-xs tabular-nums leading-tight",
              isSelected
                ? "font-medium text-primary"
                : "text-muted-foreground",
            )}
          >
            {pageNum}
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="text-[11px]">
        Page {pageNum}
        {hasEdit && editType?.hasProgram && editType?.hasVisual && " · programmatic + visual edits"}
        {hasEdit && editType?.hasProgram && !editType?.hasVisual && " · programmatic edit"}
        {hasEdit && !editType?.hasProgram && editType?.hasVisual && " · visual edit"}
      </TooltipContent>
    </Tooltip>
  );
}

export default function PageThumbnails({
  session,
  currentPage,
  onSelectPage,
  pageVersions,
  pageEditTypes,
}: Props) {
  const selectedRef = useRef<HTMLDivElement>(null);

  // Scroll selected thumbnail into view on page change
  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [currentPage]);

  const pages = Array.from({ length: session.page_count }, (_, i) => i + 1);

  return (
    <ScrollArea className="h-full">
      <div className="py-2 px-[14px]">
        {pages.map((pageNum) => {
          const version = pageVersions?.[pageNum];
          const hasEdit = version !== undefined && version > 0;
          const editType = pageEditTypes?.[pageNum];
          const isSelected = pageNum === currentPage;

          return (
            <div key={pageNum} ref={isSelected ? selectedRef : undefined}>
              <Thumbnail
                sessionId={session.session_id}
                pageNum={pageNum}
                version={version}
                isSelected={isSelected}
                editType={editType}
                hasEdit={hasEdit}
                onSelect={() => onSelectPage(pageNum)}
              />
            </div>
          );
        })}
      </div>
    </ScrollArea>
  );
}
