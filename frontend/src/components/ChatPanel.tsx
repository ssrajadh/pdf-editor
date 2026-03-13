import { useState, useRef, useEffect } from "react";
import { Send, Loader2, CheckCircle2, AlertCircle, Sparkles, RotateCcw, Eye, Play } from "lucide-react";
import type { ChatMessage } from "../types";
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
  const showRetry = lastMessage?.role === "assistant" && lastMessage.content.startsWith("Error:") && !isEditing && onRetry;
  const busy = isEditing || isPreviewing;

  return (
    <div className="flex flex-col h-full">
      <div className="px-4 py-3 border-b bg-white">
        <h2 className="font-semibold text-gray-900 text-sm">Edit Chat</h2>
        <p className="text-xs text-gray-400 mt-0.5">Page {currentPage}</p>
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && <EmptyState onSelect={(s) => setInput(s)} />}

        {messages.map((msg) => (
          <div key={msg.id} className="animate-fade-in">
            <MessageBubble message={msg} onExecutePlan={onExecutePlan} />
          </div>
        ))}

        {showRetry && (
          <div className="flex justify-start animate-fade-in">
            <button
              onClick={onRetry}
              className="flex items-center gap-1.5 text-xs text-blue-600 hover:text-blue-700
                         px-3 py-1.5 rounded-lg hover:bg-blue-50 transition-colors"
            >
              <RotateCcw className="w-3 h-3" />
              Retry last edit
            </button>
          </div>
        )}
      </div>

      <div className="p-3 border-t bg-white">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Describe your edit for page ${currentPage}...`}
            disabled={busy}
            rows={1}
            className="flex-1 resize-none rounded-lg border border-gray-300 px-3 py-2 text-sm
                       focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent
                       disabled:bg-gray-100 disabled:text-gray-400
                       max-h-32 min-h-[38px]"
            onInput={(e) => {
              const el = e.currentTarget;
              el.style.height = "auto";
              el.style.height = Math.min(el.scrollHeight, 128) + "px";
            }}
          />
          {onPreviewPlan && (
            <button
              onClick={handlePreview}
              disabled={busy || !input.trim()}
              title="Preview plan without executing"
              className="shrink-0 w-9 h-9 flex items-center justify-center rounded-lg
                         border border-gray-300 text-gray-500 hover:text-blue-600
                         hover:border-blue-300 hover:bg-blue-50
                         disabled:border-gray-200 disabled:text-gray-300 disabled:cursor-not-allowed
                         transition-colors"
            >
              {isPreviewing ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Eye className="w-4 h-4" />
              )}
            </button>
          )}
          <button
            onClick={handleSubmit}
            disabled={busy || !input.trim()}
            title="Send (Enter)"
            className="shrink-0 w-9 h-9 flex items-center justify-center rounded-lg
                       bg-blue-600 text-white hover:bg-blue-700
                       disabled:bg-gray-300 disabled:cursor-not-allowed
                       transition-colors"
          >
            {isEditing ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1.5 text-right">
          Enter to send &middot; Shift+Enter for newline
        </p>
      </div>
    </div>
  );
}

function EmptyState({ onSelect }: { onSelect: (s: string) => void }) {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center gap-4 py-8">
      <Sparkles className="w-10 h-10 text-gray-300" />
      <div>
        <p className="text-sm text-gray-500 mb-4">
          Describe how you'd like to edit this page
        </p>
        <div className="space-y-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => onSelect(s)}
              className="block w-full text-left text-xs px-3 py-2 rounded-lg
                         bg-gray-100 text-gray-600 hover:bg-blue-50 hover:text-blue-700
                         transition-colors"
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
  const phaseIcon = isFastPhase ? "⚡" : isSlowPhase ? "🎨" : null;

  return (
    <div className="flex justify-center">
      <div
        className={`flex items-center gap-2 text-xs px-3 py-1.5 rounded-full transition-colors ${
          isFastPhase
            ? "text-green-700 bg-green-50"
            : isSlowPhase
              ? "text-blue-700 bg-blue-50"
              : "text-gray-500 bg-gray-100"
        }`}
      >
        <Loader2
          className={`w-3 h-3 animate-spin ${
            isFastPhase ? "text-green-500" : isSlowPhase ? "text-blue-500" : ""
          }`}
        />
        <span>
          {hasOpInfo && (
            <span className="font-medium">
              {phaseIcon} {op_index + 1}/{total_ops}:{" "}
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
    text_replace: { icon: "⚡", label: "Text Replace" },
    style_change: { icon: "⚡", label: "Style Change" },
    visual_regenerate: { icon: "🎨", label: "Visual Edit" },
  };

  return (
    <div className="flex justify-start">
      <div className="max-w-[95%] px-3 py-2 rounded-2xl rounded-bl-md text-sm bg-gray-100 text-gray-800">
        <div className="flex items-center gap-1.5 mb-2">
          <Eye className="w-3.5 h-3.5 text-blue-600 shrink-0" />
          <span className="font-medium">Plan Preview</span>
        </div>

        <div className="rounded-lg border border-gray-200 bg-white text-xs overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-100 text-gray-600 font-medium">
            {plan.summary}
          </div>

          {plan.all_programmatic && (
            <div className="px-3 py-1.5 bg-green-50 border-b border-green-100 text-green-700 text-[11px] font-medium">
              ⚡ Fast path — all operations are programmatic
            </div>
          )}

          <div className="px-3 py-1 divide-y divide-gray-50">
            {plan.operations.map((op, idx) => {
              const config = TYPE_LABELS[op.type] ?? TYPE_LABELS.visual_regenerate;
              return (
                <div key={idx} className="py-1.5">
                  <div className="flex items-center gap-2">
                    <span className="text-sm">{config.icon}</span>
                    <span className="font-medium text-gray-700">
                      #{idx + 1} {config.label}
                    </span>
                    <span
                      className={`ml-auto text-[10px] font-medium tabular-nums ${
                        op.confidence >= 0.8
                          ? "text-green-600"
                          : op.confidence >= 0.5
                            ? "text-yellow-600"
                            : "text-red-500"
                      }`}
                    >
                      {Math.round(op.confidence * 100)}%
                    </span>
                  </div>
                  <div className="text-[10px] text-gray-400 ml-6 mt-0.5">
                    {op.reasoning}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="px-3 py-2 border-t border-gray-100 text-[11px] text-gray-500">
            Order: {plan.execution_order.map((i) => `#${i + 1}`).join(" → ")}
          </div>
        </div>

        {onExecute && message.previewPrompt && (
          <button
            onClick={() => onExecute(message.previewPrompt!)}
            className="mt-2 flex items-center gap-1.5 text-xs font-medium text-white
                       bg-blue-600 hover:bg-blue-700 px-3 py-1.5 rounded-lg
                       transition-colors w-full justify-center"
          >
            <Play className="w-3 h-3" />
            Execute this plan
          </button>
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
        <div className="max-w-[85%] bg-blue-600 text-white px-3 py-2 rounded-2xl rounded-br-md text-sm whitespace-pre-wrap">
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
        className={`max-w-[95%] px-3 py-2 rounded-2xl rounded-bl-md text-sm ${
          isError ? "bg-red-50 text-red-700" : "bg-gray-100 text-gray-800"
        }`}
      >
        <div className="flex items-center gap-1.5">
          {isError ? (
            <AlertCircle className="w-3.5 h-3.5 shrink-0" />
          ) : (
            <CheckCircle2 className="w-3.5 h-3.5 text-green-600 shrink-0" />
          )}
          <span>{message.content}</span>
        </div>

        {result && (
          <>
            <div className="mt-1 text-xs text-gray-500">
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
