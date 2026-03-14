import { cn } from "@/lib/utils";
import type { ExecutionResult, OperationResult } from "@/types";

function formatTime(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

const PATH_CONFIG: Record<string, { label: string; badgeClass: string }> = {
  programmatic: {
    label: "programmatic",
    badgeClass: "bg-green-500/10 text-green-500",
  },
  visual: {
    label: "visual",
    badgeClass: "bg-blue-500/10 text-blue-500",
  },
  fallback_visual: {
    label: "fallback",
    badgeClass: "bg-orange-500/10 text-orange-500",
  },
};

function OperationRow({ op }: { op: OperationResult }) {
  const config = PATH_CONFIG[op.path] ?? PATH_CONFIG.visual;
  const isFallback = op.path === "fallback_visual";

  return (
    <div className="flex items-start gap-2 py-1.5">
      <span
        className={cn(
          "mt-0.5 h-4 w-4 shrink-0 rounded-full text-[9px] font-bold text-white flex items-center justify-center",
          op.path === "programmatic" ? "bg-green-500" : "bg-blue-500",
          isFallback && "bg-orange-500",
        )}
      >
        {op.path === "programmatic" ? "P" : isFallback ? "F" : "V"}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-xs">{op.detail}</span>
          <span className="shrink-0 font-mono text-[10px] text-muted-foreground tabular-nums">
            {formatTime(op.time_ms)}
          </span>
        </div>
        <span
          className={cn(
            "mt-0.5 inline-block rounded px-1.5 py-0.5 text-[10px] font-medium",
            config.badgeClass,
          )}
        >
          {config.label}
        </span>
        {isFallback && op.error && (
          <div className="mt-1 text-[10px] leading-tight text-orange-500">
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
    <div className="mt-2 overflow-hidden rounded-lg border bg-card text-xs">
      <div className="border-b px-3 py-2 text-muted-foreground">
        {result.plan_summary}
      </div>

      {allProgrammatic && (
        <div className="border-b bg-green-500/10 px-3 py-2 font-medium text-green-500">
          Completed in {formatTime(result.total_time_ms)} — no AI model needed
        </div>
      )}

      <div className="divide-y divide-border px-3 py-1">
        {result.operations.map((op) => (
          <OperationRow key={op.op_index} op={op} />
        ))}
      </div>

      <div className="border-t px-3 py-2 text-[11px] text-muted-foreground tabular-nums font-mono">
        {result.operations.length} {result.operations.length === 1 ? "op" : "ops"}
        {result.programmatic_count > 0 && result.visual_count > 0 && (
          <>
            : {result.programmatic_count} prog ({formatTime(progTime)})
            {" + "}
            {result.visual_count} vis ({formatTime(visTime)})
            {" = "}
          </>
        )}
        {(result.programmatic_count === 0 || result.visual_count === 0) && " · "}
        {formatTime(result.total_time_ms)} total
      </div>
    </div>
  );
}
