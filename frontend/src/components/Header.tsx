import { Download, FileText, Loader2, Moon, Sun, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";
import { useTheme } from "@/components/ThemeProvider";
import type { Session } from "@/types";

interface HeaderProps {
  session: Session;
  currentPage: number;
  hasEdits: boolean;
  exporting: boolean;
  onExport: () => void;
  onUploadNew: () => void;
}

export default function Header({
  session,
  currentPage,
  hasEdits,
  exporting,
  onExport,
  onUploadNew,
}: HeaderProps) {
  const { theme, setTheme } = useTheme();
  const isDark = theme === "dark";

  return (
    <header className="flex h-12 items-center border-b bg-background px-4 shrink-0">
      {/* Left: App name */}
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4 text-blue-500" />
        <span className="text-sm font-semibold tracking-tight">
          Nano PDF Studio
        </span>
      </div>

      <Separator orientation="vertical" className="mx-3 h-5" />

      {/* Center: filename + page indicator */}
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <span className="font-medium text-foreground truncate max-w-[200px]">
          {session.filename}
        </span>
        <span>
          — Page {currentPage} of {session.page_count}
        </span>
      </div>

      {/* Right: actions */}
      <div className="ml-auto flex items-center gap-1">
        {hasEdits && (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                onClick={onExport}
                disabled={exporting}
                className="gap-1.5"
              >
                {exporting ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Download className="h-3.5 w-3.5" />
                )}
                {exporting ? "Exporting..." : "Download PDF"}
              </Button>
            </TooltipTrigger>
            <TooltipContent>Download edited PDF</TooltipContent>
          </Tooltip>
        )}

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" onClick={onUploadNew}>
              <Upload className="h-4 w-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>Upload new PDF</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme(isDark ? "light" : "dark")}
            >
              {isDark ? (
                <Sun className="h-4 w-4" />
              ) : (
                <Moon className="h-4 w-4" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent>
            {isDark ? "Switch to light mode" : "Switch to dark mode"}
          </TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
