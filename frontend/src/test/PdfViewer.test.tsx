import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import PdfViewer from "../components/PdfViewer";
import type { Session, EditProgress } from "../types";

// Mock the BeforeAfterToggle since it's a simple child component
vi.mock("../components/BeforeAfterToggle", () => ({
  default: ({ showOriginal, onToggle }: { showOriginal: boolean; onToggle: (v: boolean) => void }) => (
    <button data-testid="before-after" onClick={() => onToggle(!showOriginal)}>
      {showOriginal ? "Showing original" : "Showing edited"}
    </button>
  ),
}));

const defaultSession: Session = {
  session_id: "test-session",
  page_count: 5,
  filename: "test.pdf",
};

function renderViewer(overrides: Record<string, unknown> = {}) {
  const defaults = {
    session: defaultSession,
    currentPage: 1,
    imageUrl: "/api/pdf/test-session/page/1/image?step=1",
    originalImageUrl: "/api/pdf/test-session/page/1/image?step=0",
    pageVersion: 1,
    isEditing: false,
    editProgress: null as EditProgress | null,
    hasEditWarnings: false,
  };
  return render(<PdfViewer {...{ ...defaults, ...overrides }} />);
}

describe("PdfViewer", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the page image with correct alt text", () => {
    renderViewer();
    const img = screen.getByAltText("Page 1");
    expect(img).toBeInTheDocument();
  });

  it("shows correct page indicator", () => {
    renderViewer({ currentPage: 3 });
    expect(screen.getByText("Page 3")).toBeInTheDocument();
    expect(screen.getByText(/of 5/)).toBeInTheDocument();
  });

  it("shows 'Failed to load page' and retry button on image error", () => {
    renderViewer();
    const img = screen.getByAltText("Page 1");

    fireEvent.error(img);

    expect(screen.getByText("Failed to load page")).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });

  it("retries loading after clicking retry", () => {
    renderViewer();
    const img = screen.getByAltText("Page 1");

    fireEvent.error(img);

    const retryBtn = screen.getByText("Retry");
    fireEvent.click(retryBtn);

    // After retry, error state should reset — image should reappear
    const newImg = screen.getByAltText("Page 1");
    expect(newImg).toBeInTheDocument();
  });

  it("shows loading state initially", () => {
    renderViewer();
    // The image starts with opacity-0 while loading
    const img = screen.getByAltText("Page 1");
    expect(img.className).toContain("opacity-0");
  });

  it("removes loading state after image loads", () => {
    renderViewer();
    const img = screen.getByAltText("Page 1");

    fireEvent.load(img);

    expect(img.className).toContain("opacity-100");
  });

  it("shows edit progress overlay when editing", () => {
    const progress: EditProgress = {
      stage: "programmatic",
      message: "Replacing text...",
      timestamp: new Date().toISOString(),
    };

    renderViewer({ isEditing: true, editProgress: progress });
    expect(screen.getByText("Replacing text...")).toBeInTheDocument();
    expect(screen.getByText("programmatic")).toBeInTheDocument();
  });

  it("shows before/after toggle when page has edits", () => {
    renderViewer({ pageVersion: 2 });
    expect(screen.getByTestId("before-after")).toBeInTheDocument();
  });

  it("hides before/after toggle when no edits", () => {
    renderViewer({ pageVersion: 0 });
    expect(screen.queryByTestId("before-after")).not.toBeInTheDocument();
  });

  it("hides before/after toggle when pageVersion is undefined", () => {
    renderViewer({ pageVersion: undefined });
    expect(screen.queryByTestId("before-after")).not.toBeInTheDocument();
  });

  it("uses edited image URL by default", () => {
    renderViewer({
      imageUrl: "/edited",
      originalImageUrl: "/original",
      pageVersion: 1,
    });
    const img = screen.getByAltText("Page 1");
    expect(img).toHaveAttribute("src", "/edited");
  });

  it("resets image error state on page change", () => {
    const { rerender } = render(
      <PdfViewer
        session={defaultSession}
        currentPage={1}
        imageUrl="/img1"
        originalImageUrl="/orig1"
        isEditing={false}
        editProgress={null}
      />,
    );

    // Trigger error on page 1
    fireEvent.error(screen.getByAltText("Page 1"));
    expect(screen.getByText("Failed to load page")).toBeInTheDocument();

    // Change to page 2 — error should clear
    rerender(
      <PdfViewer
        session={defaultSession}
        currentPage={2}
        imageUrl="/img2"
        originalImageUrl="/orig2"
        isEditing={false}
        editProgress={null}
      />,
    );

    expect(screen.queryByText("Failed to load page")).not.toBeInTheDocument();
    expect(screen.getByAltText("Page 2")).toBeInTheDocument();
  });
});
