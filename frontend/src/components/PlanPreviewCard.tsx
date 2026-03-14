import { Eye, Play } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/types";

export default function PlanPreviewCard({
  message,
  onExecute,
}: {
  message: ChatMessage;
  onExecute?: (prompt: string) => void;
}) {
  const plan = message.plan!;

  return (
    <div className="flex justify-start">
      <Card className="max-w-[95%] overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-1.5 bg-muted/50 px-3 py-2 text-[12px] font-medium">
          <Eye className="h-3.5 w-3.5 text-blue-500" />
          Plan Preview
        </div>

        <div className="px-3 py-2.5 space-y-2">
          <p className="text-[12px]">{plan.summary}</p>

          {plan.all_programmatic && (
            <Badge
              variant="outline"
              className="rounded border-emerald-500/30 bg-emerald-500/10 text-emerald-500 text-[10px] font-mono"
            >
              ⚡ fast path — all programmatic
            </Badge>
          )}

          <Separator />

          {/* Operation list */}
          <div className="space-y-1.5">
            {plan.operations.map((op, idx) => {
              const isVisual = op.type === "visual_regenerate";
              return (
                <div key={idx} className="flex items-start gap-2">
                  <div
                    className={cn(
                      "mt-0.5 h-4 w-4 shrink-0 rounded text-[9px] font-bold text-white flex items-center justify-center",
                      isVisual ? "bg-blue-500" : "bg-emerald-600",
                    )}
                  >
                    {idx + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 text-[12px]">
                      <span>
                        {isVisual ? "🎨" : "⚡"}{" "}
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

                    {op.type === "text_replace" && op.original_text && (
                      <div className="mt-0.5 font-mono text-[10px]">
                        <span className="rounded-sm bg-red-500/10 px-0.5 text-red-400">
                          {op.original_text}
                        </span>
                        <span className="mx-1 text-muted-foreground/30">→</span>
                        <span className="rounded-sm bg-emerald-500/10 px-0.5 text-emerald-400">
                          {op.replacement_text}
                        </span>
                      </div>
                    )}

                    <p className="mt-0.5 text-[10px] text-muted-foreground/40">
                      {op.reasoning}
                    </p>
                  </div>
                </div>
              );
            })}
          </div>

          <Separator />

          {/* Footer: order + execute button */}
          <div className="text-[10px] font-mono text-muted-foreground">
            order: {plan.execution_order.map((i) => `#${i + 1}`).join(" → ")}
          </div>

          {onExecute && message.previewPrompt && (
            <Button
              size="sm"
              onClick={() => onExecute(message.previewPrompt!)}
              className="h-7 w-full gap-1.5 text-[12px]"
            >
              <Play className="h-3 w-3" />
              Execute this plan
            </Button>
          )}
        </div>
      </Card>
    </div>
  );
}
