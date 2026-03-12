import { useEffect } from "react";
import { CheckCircle2, X } from "lucide-react";

interface Props {
  message: string;
  onClose: () => void;
  duration?: number;
}

export default function Toast({ message, onClose, duration = 4000 }: Props) {
  useEffect(() => {
    const timer = setTimeout(onClose, duration);
    return () => clearTimeout(timer);
  }, [onClose, duration]);

  return (
    <div className="fixed bottom-6 right-6 z-50 animate-slide-up">
      <div className="flex items-center gap-2 bg-gray-900 text-white px-4 py-3 rounded-lg shadow-lg text-sm">
        <CheckCircle2 className="w-4 h-4 text-green-400 shrink-0" />
        <span>{message}</span>
        <button onClick={onClose} className="ml-2 text-gray-400 hover:text-white">
          <X className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}
