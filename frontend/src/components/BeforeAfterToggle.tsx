import { cn } from "@/lib/utils";
import type { PageEditType } from "@/types";

interface Props {
  showOriginal: boolean;
  onToggle: (showOriginal: boolean) => void;
  editType?: PageEditType;
}

function editLabel(editType?: PageEditType): string {
  if (!editType) return "Edited";
  if (editType.hasProgram && editType.hasVisual) return "Mixed";
  if (editType.hasProgram) return "Programmatic";
  if (editType.hasVisual) return "AI";
  return "Edited";
}

export default function BeforeAfterToggle({ showOriginal, onToggle, editType }: Props) {
  const label = editLabel(editType);

  return (
    <div className="inline-flex h-6 rounded bg-muted p-0.5 text-[11px] font-medium">
      <button
        onClick={() => onToggle(true)}
        className={cn(
          "rounded px-2 transition-all",
          showOriginal
            ? "bg-background text-foreground shadow-sm"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        Before
      </button>
      <button
        onClick={() => onToggle(false)}
        className={cn(
          "rounded px-2 transition-all",
          !showOriginal
            ? "bg-background text-foreground shadow-sm"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        {label}
      </button>
    </div>
  );
}
