import { describe, expect, it } from "vitest";
import { checkRequest, formatDate, statusLabel, STATUS_META, STATUS_ORDER } from "./board";

describe("checkRequest", () => {
  it("trims and accepts a good request", () => {
    const out = checkRequest({ title: "  Split bills ", body: " Across cards ", author: " MCH " });
    expect(out).toEqual({ value: { title: "Split bills", body: "Across cards", author: "MCH" } });
  });

  it("rejects a title shorter than three characters", () => {
    const out = checkRequest({ title: "no", body: "b", author: "a" });
    expect("errors" in out && out.errors.title).toBeTruthy();
  });

  it("requires a body and an author", () => {
    const out = checkRequest({ title: "A good idea", body: "   ", author: "  " });
    expect("errors" in out && out.errors.body).toBeTruthy();
    expect("errors" in out && out.errors.author).toBeTruthy();
  });

  it("caps the title length", () => {
    const out = checkRequest({ title: "x".repeat(201), body: "b", author: "a" });
    expect("errors" in out && out.errors.title).toBeTruthy();
  });
});

describe("status metadata", () => {
  it("labels every status and keeps them in lifecycle order", () => {
    expect(STATUS_ORDER).toEqual(["submitted", "under_review", "planned", "shipped"]);
    for (const status of STATUS_ORDER) expect(STATUS_META[status].label).toBeTruthy();
    expect(statusLabel("planned")).toBe("Planned");
  });
});

describe("formatDate", () => {
  it("shows a short Brisbane date", () => {
    // 2026-11-20T13:00Z is already the 20th in Brisbane (UTC+10).
    expect(formatDate("2026-11-20T13:00:00Z")).toBe("20 Nov 2026");
  });

  it("returns empty string for an unparseable value", () => {
    expect(formatDate("not-a-date")).toBe("");
  });
});
