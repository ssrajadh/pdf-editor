import { useState } from "react";
import { ChevronRight, Check, AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";
import type { ChatMessage, ExecutionResult, OperationResult, ExecutionPlan, PlanOperation } from "@/types";

function fmtTime(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}

function opIcon(op: OperationResult): string {
  if (op.path === "blocked") return "🛑";
  if (op.path === "fallback_visual") return "⚠️";
  if (op.path === "programmatic") return "⚡";
  return "🎨";
}

function opDescription(op: OperationResult): string {
  return op.detail;
}

/** Badge for the summary time — color varies by edit type. */
function TimeBadge({ result }: { result: ExecutionResult }) {
  const hasFallback = result.operations.some((o) => o.path === "fallback_visual");
  const allProg = result.visual_count === 0 && result.programmatic_count > 0;

  let icon: string;
  let cls: string;

  if (hasFallback) {
    icon = "⚠️";
    cls = "border-orange-500/30 bg-orange-500/10 text-orange-400";
  } else if (allProg) {
    icon = "⚡";
    cls = "border-emerald-500/30 bg-emerald-500/10 text-emerald-500";
  } else {
    icon = "🎨";
    cls = "border-blue-500/30 bg-blue-500/10 text-blue-400";
  }

  return (
    <Badge
      variant="outline"
      className={cn("rounded px-1.5 py-0 h-5 text-[11px] font-mono tabular-nums gap-1", cls)}
    >
      <span>{icon}</span>
      {fmtTime(result.total_time_ms)}
    </Badge>
  );
}

/** Text layer source indicator. */
function TextLayerLine({ source }: { source: ExecutionResult["text_layer_source"] }) {
  if (source === "original" || source === "programmatic_edit") {
    return (
      <div className="flex items-center gap-1 text-[11px] text-emerald-500">
        <Check className="h-3 w-3" />
        Text layer preserved
      </div>
    );
  }
  if (source === "ocr") {
    return (
      <div className="flex items-center gap-1 text-[11px] text-amber-500">
        <AlertTriangle className="h-3 w-3" />
        Text layer reconstructed via OCR
      </div>
    );
  }
  if (source === "mixed") {
    return (
      <div className="flex items-center gap-1 text-[11px] text-amber-500">
        <AlertTriangle className="h-3 w-3" />
        Text layer partially preserved
      </div>
    );
  }
  return null;
}

/** Always-visible operation rows. */
function OperationRows({ ops }: { ops: OperationResult[] }) {
  return (
    <div>
      {ops.map((op, i) => (
        <div key={op.op_index}>
          {i > 0 && <Separator className="my-0" />}
          <div className="flex items-start gap-2 py-1.5">
            <span className="mt-[1px] text-[13px] leading-none shrink-0">
              {opIcon(op)}
            </span>
            <span
              className={cn(
                "flex-1 min-w-0 text-[12px] truncate",
                op.path === "blocked" && "text-red-400",
              )}
            >
              {opDescription(op)}
            </span>
            <Badge
              variant="secondary"
              className="shrink-0 rounded h-4 px-1 text-[10px] font-mono tabular-nums"
            >
              {fmtTime(op.time_ms)}
            </Badge>
          </div>
          {op.path === "fallback_visual" && op.error && (
            <p className="ml-6 -mt-0.5 mb-1 text-[10px] text-orange-400/70">
              Programmatic failed: {op.error}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

/** Collapsible plan details. */
function PlanDetails({ plan }: { plan: ExecutionPlan }) {
  const [open, setOpen] = useState(false);

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger className="flex items-center gap-0.5 text-[11px] text-blue-500 hover:text-blue-400 transition-colors py-0.5">
        <ChevronRight
          className={cn("h-3 w-3 transition-transform", open && "rotate-90")}
        />
        {open ? "Hide" : "Show"} plan
      </CollapsibleTrigger>

      <CollapsibleContent>
        <div className="mt-1 rounded border bg-background px-2.5 py-2 text-[11px] space-y-2 animate-fade-in">
          {/* Execution order */}
          <div className="font-mono text-[10px] text-muted-foreground">
            order: {plan.execution_order.map((i) => `#${i + 1}`).join(" → ")}
            {plan.all_programmatic && (
              <span className="ml-1.5 text-emerald-500 font-medium">fast path</span>
            )}
          </div>

          {/* Per-operation detail */}
          {plan.operations.map((op, idx) => (
            <PlanOpDetail key={idx} op={op} idx={idx} />
          ))}

          {/* Page analysis */}
          {plan.page_analysis && (
            <p className="text-[10px] text-muted-foreground/50 italic border-t pt-1.5">
              {plan.page_analysis}
            </p>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function PlanOpDetail({ op, idx }: { op: PlanOperation; idx: number }) {
  const isVisual = op.type === "visual_regenerate";

  return (
    <div className="border-t border-border pt-1.5 first:border-t-0 first:pt-0">
      <div className="flex items-center gap-1.5">
        <div
          className={cn(
            "h-4 w-4 shrink-0 rounded text-[9px] font-bold text-white flex items-center justify-center",
            isVisual ? "bg-blue-500" : "bg-emerald-600",
          )}
        >
          {idx + 1}
        </div>
        <span className="font-medium text-[11px]">
          {op.type === "text_replace"
            ? "Text replace"
            : op.type === "style_change"
              ? "Style change"
              : "Visual edit"}
        </span>
        <span
          className={cn(
            "ml-auto font-mono text-[10px] tabular-nums",
            op.confidence >= 0.8
              ? "text-emerald-500"
              : op.confidence >= 0.5
                ? "text-yellow-500"
                : "text-red-500",
          )}
        >
          {Math.round(op.confidence * 100)}%
        </span>
      </div>

      {/* Text replace diff */}
      {op.type === "text_replace" && op.original_text && (
        <div className="ml-5.5 mt-0.5 font-mono text-[10px]">
          <span className="rounded-sm bg-red-500/10 px-0.5 text-red-400">
            {op.original_text}
          </span>
          <span className="mx-1 text-muted-foreground/30">→</span>
          <span className="rounded-sm bg-emerald-500/10 px-0.5 text-emerald-400">
            {op.replacement_text}
          </span>
        </div>
      )}

      {/* Context */}
      {op.type === "text_replace" && (op.context_before || op.context_after) && (
        <div className="ml-5.5 text-[10px] text-muted-foreground/40 font-mono">
          {op.context_before && <span>…{op.context_before}</span>}
          <span className="text-muted-foreground/60">[match]</span>
          {op.context_after && <span>{op.context_after}…</span>}
        </div>
      )}

      {/* Style change */}
      {op.type === "style_change" && op.target_text && (
        <div className="ml-5.5 text-[10px] text-muted-foreground font-mono">
          target: {op.target_text}
          {op.changes && (
            <span className="text-muted-foreground/40">
              {" "}· {Object.keys(op.changes).join(", ")}
            </span>
          )}
        </div>
      )}

      {/* Visual prompt */}
      {op.type === "visual_regenerate" && op.prompt && (
        <div className="ml-5.5 text-[10px] italic text-muted-foreground">
          "{op.prompt}"
        </div>
      )}

      {/* Reasoning */}
      <p className="ml-5.5 mt-0.5 text-[10px] text-muted-foreground/40">
        {op.reasoning}
      </p>
    </div>
  );
}

/* ================================================================
   Main export: ResultCard
   ================================================================ */

export default function ResultCard({
  message,
  onForceEdit,
}: {
  message: ChatMessage;
  onForceEdit?: (prompt: string) => void;
}) {
  const result = message.result!;
  const plan = message.plan;
  const allProg =
    result.visual_count === 0 && result.programmatic_count > 0;
  const onlyBlocked =
    result.blocked_count > 0 &&
    result.programmatic_count === 0 &&
    result.visual_count === 0;
  const blockedOps = result.operations.filter((op) => op.path === "blocked");

  return (
    <div className="flex justify-start">
      <Card
        className={cn(
          "max-w-[95%] overflow-hidden",
          allProg && "border-emerald-500/20",
        )}
      >
        {/* Hero line for all-programmatic */}
        {allProg && (
          <div className="bg-emerald-500/10 px-3 py-1.5 text-[12px] font-medium text-emerald-500">
            ✨ Completed in {fmtTime(result.total_time_ms)} — no AI model needed
          </div>
        )}

        <div className="px-3 py-2.5 space-y-2">
          {blockedOps.length > 0 && (
            <div className="space-y-2">
              {blockedOps.map((op, idx) => {
                const density =
                  op.risk_assessment?.text_density !== undefined
                    ? Math.round(op.risk_assessment.text_density * 100)
                    : null;
                const canOverride =
                  op.risk_assessment?.override_available &&
                  Boolean(message.editPrompt);

                return (
                  <Card
                    key={`blocked-${idx}`}
                    className="border-destructive/30 bg-destructive/5 px-3 py-2"
                  >
                    <div className="text-[12px] font-medium text-red-400">
                      🛑 Visual edit blocked
                      {density !== null && (
                        <span className="text-red-400/80">
                          {" "}— this page is {density}% text
                        </span>
                      )}
                    </div>
                    <p className="text-[11px] text-muted-foreground">
                      AI regeneration would corrupt the text content.
                    </p>
                    <p className="text-[11px] text-muted-foreground">
                      Suggestion: try rephrasing as a text or style change.
                    </p>
                    {canOverride && (
                      <div className="pt-1">
                        <Button
                          variant="destructive"
                          size="sm"
                          className="h-7 px-2 text-[11px]"
                          onClick={() => message.editPrompt && onForceEdit?.(message.editPrompt)}
                        >
                          Override and proceed anyway
                        </Button>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )}

          {/* Summary */}
          <div className="flex items-center gap-2">
            <span className="text-[13px]">
              {onlyBlocked ? "Edit blocked" : "Edit applied"}
            </span>
            {!onlyBlocked && <TimeBadge result={result} />}
          </div>

          {/* Plan summary text */}
          {result.plan_summary && (
            <p className="text-[12px] text-muted-foreground">
              {result.plan_summary}
            </p>
          )}

          <Separator />

          {/* Operation breakdown — always visible */}
          <OperationRows ops={result.operations} />

          {/* Text layer indicator */}
          <TextLayerLine source={result.text_layer_source} />

          {/* Expandable plan details */}
          {plan && <PlanDetails plan={plan} />}
        </div>
      </Card>
    </div>
  );
}
