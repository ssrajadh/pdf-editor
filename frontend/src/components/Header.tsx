import { Download, FileText, Loader2, Moon, Sun, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { useTheme } from "@/components/ThemeProvider";
import type { Session } from "@/types";

interface HeaderProps {
  session: Session | null;
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
    <header className="flex h-11 items-center border-b bg-panel px-3 shrink-0 select-none">
      {/* Left: branding */}
      <div className="flex items-center gap-1.5">
        <div className="flex h-6 w-6 items-center justify-center rounded bg-blue-600">
          <FileText className="h-3.5 w-3.5 text-white" />
        </div>
        <span className="text-[13px] font-semibold tracking-tight">
          Nano PDF
        </span>
      </div>

      <Separator orientation="vertical" className="mx-2.5 h-4" />

      {/* Center: file context */}
      <div className="flex items-center gap-2 text-[13px] text-muted-foreground min-w-0">
        <span className="truncate max-w-[240px] font-medium text-foreground">
          {session ? session.filename : "No file loaded"}
        </span>
        {session && (
          <Badge variant="secondary" className="rounded px-1.5 py-0 text-[11px] font-mono h-5 shrink-0">
            {currentPage}/{session.page_count}
          </Badge>
        )}
      </div>

      {/* Right: toolbar */}
      <div className="ml-auto flex items-center">
        {session && hasEdits && (
          <>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={onExport}
                  disabled={exporting}
                  className="h-7 gap-1.5 px-2.5 text-[12px]"
                >
                  {exporting ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Download className="h-3.5 w-3.5" />
                  )}
                  Export
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Download edited PDF</TooltipContent>
            </Tooltip>
            <Separator orientation="vertical" className="mx-1 h-4" />
          </>
        )}

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" onClick={onUploadNew} className="h-7 w-7">
              <Upload className="h-3.5 w-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Upload new file</TooltipContent>
        </Tooltip>

        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => setTheme(isDark ? "light" : "dark")}
              className="h-7 w-7"
            >
              {isDark ? (
                <Sun className="h-3.5 w-3.5" />
              ) : (
                <Moon className="h-3.5 w-3.5" />
              )}
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            {isDark ? "Light mode" : "Dark mode"}
          </TooltipContent>
        </Tooltip>
      </div>
    </header>
  );
}
