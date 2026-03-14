import { cn } from "@/lib/utils";
import type { PageEditType } from "@/types";

interface Props {
  showOriginal: boolean;
  onToggle: (showOriginal: boolean) => void;
  editType?: PageEditType;
}

function editLabel(editType?: PageEditType): string {
  if (!editType) return "Edited";
  if (editType.hasProgram && editType.hasVisual) return "Edited (mixed)";
  if (editType.hasProgram) return "Edited (programmatic)";
  if (editType.hasVisual) return "Edited (AI)";
  return "Edited";
}

export default function BeforeAfterToggle({ showOriginal, onToggle, editType }: Props) {
  const label = editLabel(editType);

  return (
    <div className="inline-flex rounded-lg bg-secondary p-0.5 text-sm">
      <button
        onClick={() => onToggle(true)}
        className={cn(
          "px-3 py-1 rounded-md transition-colors font-medium",
          showOriginal
            ? "bg-background text-foreground shadow-sm"
            : "text-muted-foreground hover:text-foreground",
        )}
      >
        Original
      </button>
      <button
        onClick={() => onToggle(false)}
        className={cn(
          "px-3 py-1 rounded-md transition-colors font-medium",
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
