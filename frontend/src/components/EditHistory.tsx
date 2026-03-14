import { useState } from "react";
import { History, Undo2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import type { PageHistoryResponse } from "@/types";

interface Props {
  history: PageHistoryResponse;
  isReverting: boolean;
  onRevert: (step: number) => void;
}

export default function EditHistory({ history, isReverting, onRevert }: Props) {
  const [confirmStep, setConfirmStep] = useState<number | null>(null);

  const { snapshots, current_step } = history;
  if (snapshots.length <= 1) return null;

  const handleDotClick = (step: number) => {
    if (step === current_step || isReverting) return;

    // Multi-step revert: show confirmation
    const stepsBack = current_step - step;
    if (stepsBack > 1) {
      setConfirmStep(step);
    } else {
      onRevert(step);
    }
  };

  const confirmRevert = () => {
    if (confirmStep !== null) {
      onRevert(confirmStep);
      setConfirmStep(null);
    }
  };

  return (
    <>
      <div className="flex items-center gap-2 px-3 py-2">
        <History className="h-3 w-3 shrink-0 text-muted-foreground" />
        <div className="flex items-end gap-2">
          {snapshots.map((snap) => {
            const isCurrent = snap.step === current_step;
            const isFuture = snap.step > current_step;
            const isOriginal = snap.step === 0;

            return (
              <Tooltip key={snap.step}>
                <TooltipTrigger asChild>
                  <div className="flex flex-col items-center gap-1">
                    <button
                      onClick={() => handleDotClick(snap.step)}
                      disabled={isCurrent || isReverting}
                      className={cn(
                        "relative rounded-full border-2 transition-all",
                        isCurrent ? "h-3 w-3 border-primary bg-primary" : "h-2.5 w-2.5",
                        isFuture
                          ? "border-muted-foreground/20 bg-transparent"
                          : isCurrent
                            ? ""
                            : "border-muted-foreground/40 bg-muted-foreground/20 hover:border-primary hover:bg-primary/30",
                        isReverting && "cursor-not-allowed opacity-50",
                        !isCurrent && !isReverting && !isFuture && "cursor-pointer",
                      )}
                    />
                    <span className="text-[9px] text-muted-foreground">
                      {snap.step}
                    </span>
                  </div>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-[200px] text-[11px]">
                  <p className="font-medium">
                    {isOriginal ? "Original" : `Step ${snap.step}`}
                    {isCurrent && " (current)"}
                  </p>
                  {snap.prompt && (
                    <p className="mt-0.5 text-muted-foreground truncate">
                      {snap.prompt}
                    </p>
                  )}
                </TooltipContent>
              </Tooltip>
            );
          })}
        </div>

        {/* Quick undo button */}
        {current_step > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                onClick={() => onRevert(0)}
                disabled={isReverting}
                className="ml-auto h-6 w-6"
              >
                <Undo2 className="h-3 w-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="text-[11px]">
              Revert to original
            </TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Multi-step revert confirmation */}
      <Dialog open={confirmStep !== null} onOpenChange={(open) => !open && setConfirmStep(null)}>
        <DialogContent className="sm:max-w-[340px]">
          <DialogHeader>
            <DialogTitle className="text-[14px]">Revert to step {confirmStep}?</DialogTitle>
            <DialogDescription className="text-[12px]">
              This will undo {current_step - (confirmStep ?? 0)} edit{(current_step - (confirmStep ?? 0)) > 1 ? "s" : ""}.
              You can re-apply edits afterward.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setConfirmStep(null)}>
              Cancel
            </Button>
            <Button size="sm" onClick={confirmRevert} disabled={isReverting}>
              Revert
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
