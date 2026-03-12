import { useState, useRef, useEffect } from "react";
import { Send, Loader2, CheckCircle2, AlertCircle, Sparkles, ShieldCheck, AlertTriangle } from "lucide-react";
import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
  currentPage: number;
  isEditing: boolean;
  onSendEdit: (prompt: string) => void;
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
  onSendEdit,
}: Props) {
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages]);

  const handleSubmit = () => {
    const text = input.trim();
    if (!text || isEditing) return;
    setInput("");
    onSendEdit(text);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b bg-white">
        <h2 className="font-semibold text-gray-900 text-sm">
          Edit Chat
        </h2>
        <p className="text-xs text-gray-400 mt-0.5">Page {currentPage}</p>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {messages.length === 0 && <EmptyState onSelect={(s) => { setInput(s); }} />}

        {messages.map((msg) => (
          <MessageBubble key={msg.id} message={msg} />
        ))}
      </div>

      {/* Input */}
      <div className="p-3 border-t bg-white">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={`Describe your edit for page ${currentPage}...`}
            disabled={isEditing}
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
          <button
            onClick={handleSubmit}
            disabled={isEditing || !input.trim()}
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

function MessageBubble({ message }: { message: ChatMessage }) {
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
    return (
      <div className="flex justify-center">
        <div className="flex items-center gap-2 text-xs text-gray-500 bg-gray-100 px-3 py-1.5 rounded-full">
          <Loader2 className="w-3 h-3 animate-spin" />
          {message.content}
        </div>
      </div>
    );
  }

  // assistant
  const isError = message.content.startsWith("Error:");
  const result = message.result;

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] px-3 py-2 rounded-2xl rounded-bl-md text-sm ${
          isError
            ? "bg-red-50 text-red-700"
            : "bg-gray-100 text-gray-800"
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
          <div className="mt-1.5 text-xs space-y-0.5">
            <div className="text-gray-500">Version {result.version}</div>
            {result.text_layer_preserved ? (
              <div className="flex items-center gap-1 text-green-600">
                <ShieldCheck className="w-3 h-3" />
                Text layer preserved
              </div>
            ) : (
              <div className="flex items-center gap-1 text-amber-600">
                <AlertTriangle className="w-3 h-3" />
                Text layer needs rebuild
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
