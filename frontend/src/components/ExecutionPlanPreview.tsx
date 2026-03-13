import { useState } from "react";
import { ChevronRight } from "lucide-react";
import type { ExecutionPlan, PlanOperation } from "../types";

const TYPE_LABELS: Record<string, { icon: string; label: string }> = {
  text_replace: { icon: "⚡", label: "Text Replace" },
  style_change: { icon: "⚡", label: "Style Change" },
  visual_regenerate: { icon: "🎨", label: "Visual Edit" },
};

function confidenceColor(c: number): string {
  if (c >= 0.8) return "text-green-600";
  if (c >= 0.5) return "text-yellow-600";
  return "text-red-500";
}

function OpDetail({ op, idx }: { op: PlanOperation; idx: number }) {
  const config = TYPE_LABELS[op.type] ?? TYPE_LABELS.visual_regenerate;

  return (
    <div className="py-2 border-t border-gray-100 first:border-t-0">
      <div className="flex items-center gap-2 mb-1">
        <span className="text-sm">{config.icon}</span>
        <span className="font-medium text-gray-700">
          #{idx + 1} {config.label}
        </span>
        <span className={`ml-auto text-[10px] font-medium tabular-nums ${confidenceColor(op.confidence)}`}>
          {Math.round(op.confidence * 100)}% confidence
        </span>
      </div>

      {op.type === "text_replace" && op.original_text && (
        <div className="text-[11px] text-gray-500 ml-6 space-y-0.5">
          <div>
            <span className="text-gray-400">from:</span>{" "}
            <span className="font-mono bg-red-50 text-red-700 px-1 rounded">{op.original_text}</span>
          </div>
          <div>
            <span className="text-gray-400">to:</span>{" "}
            <span className="font-mono bg-green-50 text-green-700 px-1 rounded">{op.replacement_text}</span>
          </div>
          {(op.context_before || op.context_after) && (
            <div className="text-[10px] text-gray-400 mt-0.5">
              context:{" "}
              {op.context_before && <span>…{op.context_before}</span>}
              <span className="font-medium text-gray-500">[target]</span>
              {op.context_after && <span>{op.context_after}…</span>}
            </div>
          )}
        </div>
      )}

      {op.type === "style_change" && op.target_text && (
        <div className="text-[11px] text-gray-500 ml-6">
          <span className="text-gray-400">target:</span>{" "}
          <span className="font-mono">{op.target_text}</span>
          {op.changes && (
            <span className="text-gray-400"> · {Object.keys(op.changes).join(", ")}</span>
          )}
        </div>
      )}

      {op.type === "visual_regenerate" && op.prompt && (
        <div className="text-[11px] text-gray-500 ml-6 italic">
          "{op.prompt}"
        </div>
      )}

      <div className="text-[10px] text-gray-400 ml-6 mt-1">
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
        className="flex items-center gap-1 text-[11px] text-blue-600 hover:text-blue-700 transition-colors"
      >
        <ChevronRight
          className={`w-3 h-3 transition-transform ${expanded ? "rotate-90" : ""}`}
        />
        {expanded ? "Hide" : "Show"} plan details
      </button>

      {expanded && (
        <div className="mt-1.5 rounded-lg border border-blue-100 bg-blue-50/30 px-3 py-1.5 text-xs animate-fade-in">
          <div className="text-gray-600 mb-1">
            <span className="font-medium">Execution order:</span>{" "}
            {plan.execution_order.map((i) => `#${i + 1}`).join(" → ")}
            {plan.all_programmatic && (
              <span className="ml-2 text-green-600 font-medium">⚡ Fast path</span>
            )}
          </div>

          <div className="divide-y divide-gray-100">
            {plan.operations.map((op, idx) => (
              <OpDetail key={idx} op={op} idx={idx} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
