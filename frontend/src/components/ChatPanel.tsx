import { useState, useRef, useEffect } from "react";
import {
  Send, Loader2, AlertCircle, Pencil,
  RotateCcw, Eye,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import type { ChatMessage, PageHistoryResponse } from "@/types";
import ResultCard from "./ResultCard";
import PlanPreviewCard from "./PlanPreviewCard";
import EditHistory from "./EditHistory";

interface Props {
  messages: ChatMessage[];
  currentPage: number;
  isEditing: boolean;
  isPreviewing?: boolean;
  history?: PageHistoryResponse | null;
  isReverting?: boolean;
  hasSession: boolean;
  onSendEdit: (prompt: string) => void;
  onForceEdit?: (prompt: string) => void;
  onPreviewPlan?: (prompt: string) => void;
  onExecutePlan?: (prompt: string) => void;
  onRetry?: () => void;
  onRevert?: (step: number) => void;
}

const SUGGESTION_CHIPS = [
  { label: "Change a date", template: "Change the date to " },
  { label: "Fix a typo", template: "Fix the typo: change '' to ''" },
  { label: "Redesign a section", template: "Redesign the " },
];

export default function ChatPanel({
  messages,
  currentPage,
  isEditing,
  isPreviewing,
  history,
  isReverting,
  hasSession,
  onSendEdit,
  onForceEdit,
  onPreviewPlan,
  onExecutePlan,
  onRetry,
  onRevert,
}: Props) {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll on new messages
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const handleSubmit = () => {
    const text = input.trim();
    if (!text || isEditing || isPreviewing || !hasSession) return;
    setInput("");
    onSendEdit(text);
  };

  const handlePreview = () => {
    const text = input.trim();
    if (!text || isEditing || isPreviewing || !onPreviewPlan || !hasSession) return;
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
  const disabled = busy || !hasSession;

  const editCount = messages.filter(
    (m) => m.role === "assistant" && m.result,
  ).length;

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* ---- Header ---- */}
      <div className="px-3 pt-3 pb-0 shrink-0 select-none">
        <h2 className="text-[13px] font-semibold">
          {hasSession ? `Page ${currentPage}` : "No document"}
        </h2>
        {editCount > 0 && (
          <p className="text-[11px] text-muted-foreground">
            {editCount} {editCount === 1 ? "edit" : "edits"}
          </p>
        )}
      </div>
      <Separator className="mt-2" />

      {/* ---- History timeline ---- */}
      {history && history.total_steps > 1 && onRevert && (
        <>
          <EditHistory
            history={history}
            isReverting={isReverting ?? false}
            onRevert={onRevert}
          />
          <Separator />
        </>
      )}

      {/* ---- Progress bar (indeterminate) ---- */}
      {isEditing && (
        <div className="h-[2px] w-full overflow-hidden bg-muted shrink-0">
          <div className="h-full w-1/3 animate-progress rounded-full bg-blue-500" />
        </div>
      )}

      {/* ---- Messages ---- */}
      <ScrollArea className="flex-1">
        <div ref={scrollRef} className="p-3 space-y-3">
          {messages.length === 0 && <EmptyState hasSession={hasSession} />}

          {messages.map((msg) => (
            <div key={msg.id} className="animate-fade-in">
              {msg.role === "user" && <UserBubble message={msg} />}
              {msg.role === "progress" && <ProgressBubble message={msg} />}
              {msg.role === "assistant" && msg.isPlanPreview && msg.plan && (
                <PlanPreviewCard message={msg} onExecute={onExecutePlan} />
              )}
              {msg.role === "assistant" && !msg.isPlanPreview && (
                msg.result ? (
                  <ResultCard message={msg} onForceEdit={onForceEdit} />
                ) : (
                  <ErrorOrTextBubble message={msg} />
                )
              )}
            </div>
          ))}

          {showRetry && (
            <div className="animate-fade-in">
              <Button
                variant="ghost"
                size="sm"
                onClick={onRetry}
                className="h-7 gap-1.5 px-2 text-[11px] text-muted-foreground hover:text-foreground"
              >
                <RotateCcw className="h-3 w-3" />
                Retry last edit
              </Button>
            </div>
          )}
        </div>
      </ScrollArea>

      {/* ---- Input area ---- */}
      <Separator />
      <div className="bg-panel p-2.5 shrink-0">
        <div className="flex items-end gap-1.5">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              busy
                ? "Editing..."
                : hasSession
                  ? "Describe your edit..."
                  : "Upload a PDF first..."
            }
            disabled={disabled}
            rows={1}
            className={cn(
              "flex-1 resize-none rounded-md border border-input bg-background px-2.5 py-1.5 text-[13px]",
              "placeholder:text-muted-foreground/40",
              "focus:outline-none focus:ring-1 focus:ring-ring",
              "disabled:cursor-not-allowed disabled:opacity-40",
              "max-h-[104px] min-h-[34px]",
            )}
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 104) + "px";
            }}
          />
          <div className="flex gap-1">
            {onPreviewPlan && (
              <Button
                variant="outline"
                size="icon"
                onClick={handlePreview}
                disabled={disabled || !input.trim()}
                title="Preview plan"
                className="h-8 w-8 shrink-0"
              >
                {isPreviewing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Eye className="h-3.5 w-3.5" />
                )}
              </Button>
            )}
            <Button
              size="icon"
              onClick={handleSubmit}
              disabled={disabled || !input.trim()}
              title="Send (Enter)"
              className="h-8 w-8 shrink-0"
            >
              {isEditing ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Send className="h-3.5 w-3.5" />
              )}
            </Button>
          </div>
        </div>

        {/* Suggestion chips — only on empty conversations */}
        {hasSession && messages.length === 0 && (
          <div className="mt-2 flex flex-wrap gap-1">
            {SUGGESTION_CHIPS.map((chip) => (
              <Button
                key={chip.label}
                variant="outline"
                size="sm"
                onClick={() => setInput(chip.template)}
                className="h-6 rounded-full px-2.5 text-[11px] font-normal"
              >
                {chip.label}
              </Button>
            ))}
          </div>
        )}

        <p className="mt-1.5 text-right text-[10px] text-muted-foreground/40 select-none">
          Enter send · Shift+Enter newline
        </p>
      </div>
    </div>
  );
}

/* ================================================================
   Sub-components
   ================================================================ */

function EmptyState({ hasSession }: { hasSession: boolean }) {
  if (!hasSession) {
    return (
      <div className="flex flex-col items-center justify-center py-16 text-center select-none px-4">
        <div className="mb-3 rounded-xl bg-muted p-4">
          <Pencil className="h-6 w-6 text-muted-foreground/40" />
        </div>
        <p className="text-[13px] font-medium">Start by uploading a PDF</p>
        <p className="mt-1 text-[11px] text-muted-foreground">
          Then type an edit instruction here
        </p>
        <div className="mt-4 w-full space-y-2 text-left text-[11px] text-muted-foreground">
          <div className="rounded-md border bg-background px-2.5 py-2">
            ⚡ “Change 2024 to 2025” — instant programmatic edit
          </div>
          <div className="rounded-md border bg-background px-2.5 py-2">
            🎨 “Redesign the chart” — AI-powered visual edit
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-center justify-center py-16 select-none">
      <div className="mb-3 rounded-xl bg-muted p-4">
        <Pencil className="h-6 w-6 text-muted-foreground/40" />
      </div>
      <p className="text-[13px] font-medium">Start editing this page</p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        Type an instruction below to make changes
      </p>
    </div>
  );
}

function UserBubble({ message }: { message: ChatMessage }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%]">
        <div className="rounded-lg rounded-br-sm bg-primary px-3 py-2 text-[13px] text-primary-foreground whitespace-pre-wrap break-words">
          {message.content}
        </div>
        <p className="mt-0.5 text-right text-[10px] text-muted-foreground/40">
          {formatTime(message.timestamp)}
        </p>
      </div>
    </div>
  );
}

function ProgressBubble({ message }: { message: ChatMessage }) {
  const { stage, op_index, total_ops } = message;
  const isFast = stage === "programmatic";
  const isSlow = stage === "generating";
  const isWarning = stage === "warning" || stage === "caution";
  const isBlocked = stage === "blocked";

  const hasOpInfo =
    op_index !== undefined && total_ops !== undefined && total_ops > 0;

  return (
    <div className="flex justify-center">
      <div
        className={cn(
          "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[11px] transition-all",
          isBlocked
            ? "bg-destructive/10 text-red-400"
            : isWarning
              ? "bg-amber-500/10 text-amber-500"
              : isFast
                ? "bg-emerald-500/10 text-emerald-500"
                : isSlow
                  ? "bg-blue-500/10 text-blue-400"
                  : "bg-muted text-muted-foreground",
        )}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        {hasOpInfo && (
          <span className="font-mono font-medium tabular-nums">
            {(op_index ?? 0) + 1}/{total_ops}
          </span>
        )}
        <span className="truncate max-w-[180px]">{message.content}</span>
      </div>
    </div>
  );
}

function ErrorOrTextBubble({ message }: { message: ChatMessage }) {
  const isError = message.content.startsWith("Error:");

  if (isError) {
    return (
      <div className="flex justify-start">
        <Card className="max-w-[95%] border-destructive/30 bg-destructive/5 px-3 py-2">
          <div className="flex items-start gap-1.5 text-[13px] text-red-400 break-words">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>{message.content.replace(/^Error:\s*/, "")}</span>
          </div>
        </Card>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div className="max-w-[95%] text-[13px] text-muted-foreground break-words">
        {message.content}
      </div>
    </div>
  );
}

/* ---- Helpers ---- */

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}
