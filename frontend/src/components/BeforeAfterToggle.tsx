import { Clock, Pencil, AlertTriangle } from "lucide-react";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

interface Props {
  showOriginal: boolean;
  onToggle: (showOriginal: boolean) => void;
  pageVersion?: number;
  hasWarnings?: boolean;
}

export default function BeforeAfterToggle({
  showOriginal,
  onToggle,
  pageVersion,
  hasWarnings,
}: Props) {
  const editedLabel =
    pageVersion !== undefined && pageVersion > 0
      ? `Edited (Step ${pageVersion})`
      : "Edited";

  return (
    <Tabs
      value={showOriginal ? "original" : "edited"}
      onValueChange={(v) => onToggle(v === "original")}
    >
      <TabsList className="h-7 p-0.5 bg-muted">
        <TabsTrigger value="original" className="h-6 px-2.5 text-[11px] gap-1.5">
          <Clock className="h-3 w-3" />
          Original
        </TabsTrigger>
        <TabsTrigger value="edited" className="h-6 px-2.5 text-[11px] gap-1.5">
          <Pencil className="h-3 w-3" />
          {editedLabel}
          {hasWarnings && (
            <AlertTriangle className="ml-1 h-3 w-3 text-amber-500" />
          )}
        </TabsTrigger>
      </TabsList>
    </Tabs>
  );
}
