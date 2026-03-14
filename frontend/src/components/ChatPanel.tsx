import { useState, useRef, useEffect } from "react";
import {
  Send, Loader2, CheckCircle2, AlertCircle, Sparkles,
  RotateCcw, Eye, Play,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ChatMessage } from "@/types";
import OperationBreakdown from "./OperationBreakdown";
import ExecutionPlanPreview from "./ExecutionPlanPreview";

interface Props {
  messages: ChatMessage[];
  currentPage: number;
  isEditing: boolean;
  isPreviewing?: boolean;
  onSendEdit: (prompt: string) => void;
  onPreviewPlan?: (prompt: string) => void;
  onExecutePlan?: (prompt: string) => void;
  onRetry?: () => void;
}

const SUGGESTIONS = [
  "Change the title to Q4 Results",
  "Make the background light blue",
  "Remove the watermark",
  "Increase the font size of all headings",
];

export default function ChatPanel({
  messages,
  currentPage,
  isEditing,
  isPreviewing,
  onSendEdit,
  onPreviewPlan,
  onExecutePlan,
  onRetry,
}: Props) {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const handleSubmit = () => {
    const text = input.trim();
    if (!text || isEditing || isPreviewing) return;
    setInput("");
    onSendEdit(text);
  };

  const handlePreview = () => {
    const text = input.trim();
    if (!text || isEditing || isPreviewing || !onPreviewPlan) return;
    setInput("");
    onPreviewPlan(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const lastMessage = messages[messages.length - 1];
  const showRetry =
    lastMessage?.role === "assistant" &&
    lastMessage.content.startsWith("Error:") &&
    !isEditing &&
    onRetry;
  const busy = isEditing || isPreviewing;

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="border-b bg-background px-4 py-3">
        <h2 className="text-sm font-semibold">Edit Chat</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">Page {currentPage}</p>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && <EmptyState onSelect={(s) => setInput(s)} />}

        {messages.map((msg) => (
          <div key={msg.id} className="animate-fade-in">
            <MessageBubble message={msg} onExecutePlan={onExecutePlan} />
          </div>
        ))}

        {showRetry && (
          <div className="flex justify-start animate-fade-in">
            <Button variant="ghost" size="sm" onClick={onRetry} className="gap-1.5 text-xs">
              <RotateCcw className="h-3 w-3" />
              Retry last edit
            </Button>
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t bg-background p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Describe your edit for page ${currentPage}...`}
            disabled={busy}
            rows={1}
            className={cn(
              "flex-1 resize-none rounded-md border border-input bg-background px-3 py-2 text-sm",
              "placeholder:text-muted-foreground",
              "focus:outline-none focus:ring-2 focus:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-50",
              "max-h-32 min-h-[38px]",
            )}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 128) + "px";
            }}
          />
          {onPreviewPlan && (
            <Button
              variant="outline"
              size="icon"
              onClick={handlePreview}
              disabled={busy || !input.trim()}
              title="Preview plan without executing"
              className="h-9 w-9 shrink-0"
            >
              {isPreviewing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </Button>
          )}
          <Button
            size="icon"
            onClick={handleSubmit}
            disabled={busy || !input.trim()}
            title="Send (Enter)"
            className="h-9 w-9 shrink-0 bg-blue-600 text-white hover:bg-blue-700"
          >
            {isEditing ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Send className="h-4 w-4" />
            )}
          </Button>
        </div>
        <p className="mt-1.5 text-right text-[10px] text-muted-foreground">
          Enter to send &middot; Shift+Enter for newline
        </p>
      </div>
    </div>
  );
}

function EmptyState({ onSelect }: { onSelect: (s: string) => void }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-4 py-8 text-center">
      <Sparkles className="h-10 w-10 text-muted-foreground/30" />
      <div>
        <p className="mb-4 text-sm text-muted-foreground">
          Describe how you'd like to edit this page
        </p>
        <div className="space-y-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => onSelect(s)}
              className={cn(
                "block w-full text-left text-xs px-3 py-2 rounded-md",
                "bg-secondary text-secondary-foreground",
                "hover:bg-accent hover:text-accent-foreground",
                "transition-colors",
              )}
            >
              "{s}"
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function ProgressBubble({ message }: { message: ChatMessage }) {
  const { stage, op_index, total_ops } = message;
  const isFastPhase = stage === "programmatic";
  const isSlowPhase = stage === "generating";

  const hasOpInfo = op_index !== undefined && total_ops !== undefined && total_ops > 0;

  return (
    <div className="flex justify-center">
      <div
        className={cn(
          "flex items-center gap-2 text-xs px-3 py-1.5 rounded-full transition-colors",
          isFastPhase
            ? "text-green-400 bg-green-500/10"
            : isSlowPhase
              ? "text-blue-400 bg-blue-500/10"
              : "text-muted-foreground bg-muted",
        )}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        <span>
          {hasOpInfo && (
            <span className="font-medium font-mono">
              {(op_index ?? 0) + 1}/{total_ops}:{" "}
            </span>
          )}
          {message.content}
        </span>
      </div>
    </div>
  );
}

function PlanPreviewCard({
  message,
  onExecute,
}: {
  message: ChatMessage;
  onExecute?: (prompt: string) => void;
}) {
  const plan = message.plan!;

  const TYPE_LABELS: Record<string, { icon: string; label: string }> = {
    text_replace: { icon: "P", label: "Text Replace" },
    style_change: { icon: "P", label: "Style Change" },
    visual_regenerate: { icon: "V", label: "Visual Edit" },
  };

  return (
    <div className="flex justify-start">
      <div className="max-w-[95%] rounded-2xl rounded-bl-md bg-muted px-3 py-2 text-sm">
        <div className="mb-2 flex items-center gap-1.5">
          <Eye className="h-3.5 w-3.5 shrink-0 text-blue-500" />
          <span className="font-medium">Plan Preview</span>
        </div>

        <div className="overflow-hidden rounded-lg border bg-card text-xs">
          <div className="border-b px-3 py-2 font-medium text-card-foreground">
            {plan.summary}
          </div>

          {plan.all_programmatic && (
            <div className="border-b bg-green-500/10 px-3 py-1.5 text-[11px] font-medium text-green-500">
              Fast path — all operations are programmatic
            </div>
          )}

          <div className="divide-y divide-border px-3 py-1">
            {plan.operations.map((op, idx) => {
              const config = TYPE_LABELS[op.type] ?? TYPE_LABELS.visual_regenerate;
              return (
                <div key={idx} className="py-1.5">
                  <div className="flex items-center gap-2">
                    <span
                      className={cn(
                        "h-4 w-4 rounded-full text-[9px] font-bold text-white flex items-center justify-center",
                        op.type === "visual_regenerate" ? "bg-blue-500" : "bg-green-500",
                      )}
                    >
                      {config.icon}
                    </span>
                    <span className="font-medium">
                      #{idx + 1} {config.label}
                    </span>
                    <span
                      className={cn(
                        "ml-auto font-mono text-[10px] font-medium tabular-nums",
                        op.confidence >= 0.8
                          ? "text-green-500"
                          : op.confidence >= 0.5
                            ? "text-yellow-500"
                            : "text-red-500",
                      )}
                    >
                      {Math.round(op.confidence * 100)}%
                    </span>
                  </div>
                  {op.type === "text_replace" && op.original_text && (
                    <div className="ml-6 mt-0.5 space-y-0.5 text-[10px] text-muted-foreground">
                      <div>
                        <span className="rounded bg-red-500/10 px-1 font-mono text-red-400">
                          {op.original_text}
                        </span>
                        {" → "}
                        <span className="rounded bg-green-500/10 px-1 font-mono text-green-400">
                          {op.replacement_text}
                        </span>
                      </div>
                      {(op.context_before || op.context_after) && (
                        <div className="text-muted-foreground/60">
                          context: {op.context_before && <span>…{op.context_before}</span>}
                          <span className="font-medium">[target]</span>
                          {op.context_after && <span>{op.context_after}…</span>}
                        </div>
                      )}
                    </div>
                  )}
                  <div className="ml-6 mt-0.5 text-[10px] text-muted-foreground/60">
                    {op.reasoning}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="space-y-1 border-t px-3 py-2 text-[11px] text-muted-foreground">
            <div>Order: {plan.execution_order.map((i) => `#${i + 1}`).join(" → ")}</div>
            {plan.page_analysis && (
              <div className="italic text-muted-foreground/60">{plan.page_analysis}</div>
            )}
          </div>
        </div>

        {onExecute && message.previewPrompt && (
          <Button
            size="sm"
            onClick={() => onExecute(message.previewPrompt!)}
            className="mt-2 w-full gap-1.5 bg-blue-600 text-white hover:bg-blue-700"
          >
            <Play className="h-3 w-3" />
            Execute this plan
          </Button>
        )}
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  onExecutePlan,
}: {
  message: ChatMessage;
  onExecutePlan?: (prompt: string) => void;
}) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-2xl rounded-br-md bg-blue-600 px-3 py-2 text-sm text-white whitespace-pre-wrap">
          {message.content}
        </div>
      </div>
    );
  }

  if (message.role === "progress") {
    return <ProgressBubble message={message} />;
  }

  if (message.isPlanPreview && message.plan) {
    return <PlanPreviewCard message={message} onExecute={onExecutePlan} />;
  }

  const isError = message.content.startsWith("Error:");
  const result = message.result;

  return (
    <div className="flex justify-start">
      <div
        className={cn(
          "max-w-[95%] rounded-2xl rounded-bl-md px-3 py-2 text-sm",
          isError ? "bg-destructive/10 text-red-400" : "bg-muted text-foreground",
        )}
      >
        <div className="flex items-center gap-1.5">
          {isError ? (
            <AlertCircle className="h-3.5 w-3.5 shrink-0" />
          ) : (
            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-green-500" />
          )}
          <span>{message.content}</span>
        </div>

        {result && (
          <>
            <div className="mt-1 text-xs text-muted-foreground">
              Version {result.version}
            </div>
            <OperationBreakdown result={result} />
          </>
        )}

        {message.plan && !message.isPlanPreview && (
          <ExecutionPlanPreview plan={message.plan} />
        )}
      </div>
    </div>
  );
}
