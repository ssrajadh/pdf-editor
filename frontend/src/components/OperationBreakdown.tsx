import type { ExecutionResult, OperationResult } from "../types";

function formatTime(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

const PATH_CONFIG: Record<string, { icon: string; badge: string; badgeClass: string }> = {
  programmatic: {
    icon: "⚡",
    badge: "programmatic",
    badgeClass: "bg-green-100 text-green-700",
  },
  visual: {
    icon: "🎨",
    badge: "visual",
    badgeClass: "bg-blue-100 text-blue-700",
  },
  fallback_visual: {
    icon: "⚠️",
    badge: "fallback",
    badgeClass: "bg-orange-100 text-orange-700",
  },
};

function OperationRow({ op }: { op: OperationResult }) {
  const config = PATH_CONFIG[op.path] ?? PATH_CONFIG.visual;
  const isFallback = op.path === "fallback_visual";

  return (
    <div className="flex items-start gap-2 py-1.5">
      <span className="text-sm shrink-0 leading-5">{config.icon}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-xs text-gray-700 truncate">{op.detail}</span>
          <span className="text-[10px] text-gray-400 shrink-0 tabular-nums">
            {formatTime(op.time_ms)}
          </span>
        </div>
        <span
          className={`inline-block mt-0.5 text-[10px] font-medium px-1.5 py-0.5 rounded ${config.badgeClass}`}
        >
          {config.badge}
        </span>
        {isFallback && op.error && (
          <div className="mt-1 text-[10px] text-orange-600 leading-tight">
            Programmatic edit failed: {op.error} — completed via AI instead
          </div>
        )}
      </div>
    </div>
  );
}

export default function OperationBreakdown({ result }: { result: ExecutionResult }) {
  const allProgrammatic = result.visual_count === 0 && result.programmatic_count > 0;

  const progTime = result.operations
    .filter((o) => o.path === "programmatic")
    .reduce((sum, o) => sum + o.time_ms, 0);
  const visTime = result.operations
    .filter((o) => o.path !== "programmatic")
    .reduce((sum, o) => sum + o.time_ms, 0);

  return (
    <div className="mt-2 rounded-lg border border-gray-200 bg-white text-xs overflow-hidden">
      {/* Plan summary */}
      <div className="px-3 py-2 border-b border-gray-100 text-gray-600">
        {result.plan_summary}
      </div>

      {/* Hero message for all-programmatic edits */}
      {allProgrammatic && (
        <div className="px-3 py-2 bg-green-50 border-b border-green-100 text-green-700 font-medium">
          ✨ Completed in {formatTime(result.total_time_ms)} — no AI model needed
        </div>
      )}

      {/* Operation list */}
      <div className="px-3 py-1 divide-y divide-gray-50">
        {result.operations.map((op) => (
          <OperationRow key={op.op_index} op={op} />
        ))}
      </div>

      {/* Totals */}
      <div className="px-3 py-2 border-t border-gray-100 text-[11px] text-gray-500 tabular-nums">
        {result.operations.length} {result.operations.length === 1 ? "operation" : "operations"}
        {result.programmatic_count > 0 && result.visual_count > 0 && (
          <>
            : {result.programmatic_count} programmatic ({formatTime(progTime)})
            {" + "}
            {result.visual_count} visual ({formatTime(visTime)})
            {" = "}
          </>
        )}
        {(result.programmatic_count === 0 || result.visual_count === 0) && " · "}
        {formatTime(result.total_time_ms)} total
      </div>
    </div>
  );
}
