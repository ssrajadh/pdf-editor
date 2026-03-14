import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

interface Props {
  showOriginal: boolean;
  onToggle: (showOriginal: boolean) => void;
  pageVersion?: number;
}

export default function BeforeAfterToggle({
  showOriginal,
  onToggle,
  pageVersion,
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
        <TabsTrigger value="original" className="h-6 px-2.5 text-[11px]">
          Original
        </TabsTrigger>
        <TabsTrigger value="edited" className="h-6 px-2.5 text-[11px]">
          {editedLabel}
        </TabsTrigger>
      </TabsList>
    </Tabs>
  );
}
