import { describe, it, expect } from "vitest";
import { getPageImageUrl } from "../services/api";

describe("getPageImageUrl", () => {
  it("includes step param when version is provided", () => {
    const url = getPageImageUrl("abc123", 1, 2);
    expect(url).toContain("/api/pdf/abc123/page/1/image");
    expect(url).toContain("step=2");
  });

  it("includes cache-busting timestamp when version is provided", () => {
    const url = getPageImageUrl("abc123", 1, 3);
    expect(url).toMatch(/t=\d+/);
  });

  it("returns base URL without query when step is undefined", () => {
    const url = getPageImageUrl("abc123", 1);
    expect(url).toBe("/api/pdf/abc123/page/1/image");
    expect(url).not.toContain("?");
  });

  it("uses step=0 for original page version", () => {
    const url = getPageImageUrl("abc123", 1, 0);
    expect(url).toContain("step=0");
  });

  it("handles multi-digit page numbers", () => {
    const url = getPageImageUrl("abc123", 42, 1);
    expect(url).toContain("/page/42/image");
  });
});
