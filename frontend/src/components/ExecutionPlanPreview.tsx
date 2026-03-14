import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import type { ExecutionPlan, PlanOperation } from "@/types";

function confidenceColor(c: number): string {
  if (c >= 0.8) return "text-green-500";
  if (c >= 0.5) return "text-yellow-500";
  return "text-red-500";
}

function OpDetail({ op, idx }: { op: PlanOperation; idx: number }) {
  const isVisual = op.type === "visual_regenerate";

  return (
    <div className="border-t border-border py-2 first:border-t-0">
      <div className="mb-1 flex items-center gap-2">
        <span
          className={cn(
            "h-4 w-4 rounded-full text-[9px] font-bold text-white flex items-center justify-center",
            isVisual ? "bg-blue-500" : "bg-green-500",
          )}
        >
          {isVisual ? "V" : "P"}
        </span>
        <span className="font-medium">
          #{idx + 1} {isVisual ? "Visual Edit" : op.type === "text_replace" ? "Text Replace" : "Style Change"}
        </span>
        <span className={cn("ml-auto font-mono text-[10px] font-medium tabular-nums", confidenceColor(op.confidence))}>
          {Math.round(op.confidence * 100)}%
        </span>
      </div>

      {op.type === "text_replace" && op.original_text && (
        <div className="ml-6 space-y-0.5 text-[11px] text-muted-foreground">
          <div>
            <span className="text-muted-foreground/60">from:</span>{" "}
            <span className="rounded bg-red-500/10 px-1 font-mono text-red-400">{op.original_text}</span>
          </div>
          <div>
            <span className="text-muted-foreground/60">to:</span>{" "}
            <span className="rounded bg-green-500/10 px-1 font-mono text-green-400">{op.replacement_text}</span>
          </div>
          {(op.context_before || op.context_after) && (
            <div className="mt-0.5 text-[10px] text-muted-foreground/50">
              context:{" "}
              {op.context_before && <span>…{op.context_before}</span>}
              <span className="font-medium text-muted-foreground">[target]</span>
              {op.context_after && <span>{op.context_after}…</span>}
            </div>
          )}
        </div>
      )}

      {op.type === "style_change" && op.target_text && (
        <div className="ml-6 text-[11px] text-muted-foreground">
          <span className="text-muted-foreground/60">target:</span>{" "}
          <span className="font-mono">{op.target_text}</span>
          {op.changes && (
            <span className="text-muted-foreground/60"> · {Object.keys(op.changes).join(", ")}</span>
          )}
        </div>
      )}

      {op.type === "visual_regenerate" && op.prompt && (
        <div className="ml-6 text-[11px] italic text-muted-foreground">
          "{op.prompt}"
        </div>
      )}

      <div className="ml-6 mt-1 text-[10px] text-muted-foreground/50">
        {op.reasoning}
      </div>
    </div>
  );
}

export default function ExecutionPlanPreview({ plan }: { plan: ExecutionPlan }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-[11px] text-blue-500 hover:text-blue-400 transition-colors"
      >
        <ChevronRight
          className={cn("h-3 w-3 transition-transform", expanded && "rotate-90")}
        />
        {expanded ? "Hide" : "Show"} plan details
      </button>

      {expanded && (
        <div className="mt-1.5 animate-fade-in rounded-lg border bg-card px-3 py-1.5 text-xs">
          <div className="mb-1 text-muted-foreground">
            <span className="font-medium">Execution order:</span>{" "}
            {plan.execution_order.map((i) => `#${i + 1}`).join(" → ")}
            {plan.all_programmatic && (
              <span className="ml-2 font-medium text-green-500">Fast path</span>
            )}
          </div>

          <div className="divide-y divide-border">
            {plan.operations.map((op, idx) => (
              <OpDetail key={idx} op={op} idx={idx} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
