// Unit tests for the small helpers (run with: pnpm test).
import { describe, expect, it } from "vitest";
import { strength } from "./components/PasswordInput";
import { ago, phLabel } from "./format";

describe("phLabel (FR-48: the label follows the building's limits)", () => {
  it.each([
    [7.2, "safe"],
    [6.6, "check"], // inside, but close to the edge
    [8.4, "check"],
    [6.4, "unsafe"],
    [8.6, "unsafe"],
    [null, "unknown"],
  ] as const)("pH %s is %s with the default 6.5 to 8.5", (ph, label) => {
    expect(phLabel(ph)).toBe(label);
  });
  it("uses the owner's own limits", () => {
    expect(phLabel(6.7, 6.8, 8.0)).toBe("unsafe");
  });
});

describe("password strength hint", () => {
  it.each([
    ["short", "weak"],
    ["aaaaaaaaaaaa", "weak"], // long but only one letter
    ["bluelamp42x", "ok"],
    ["blue lamp river", "strong"], // a passphrase with spaces
    ["correct horse battery staple", "strong"],
  ] as const)("%s is %s", (password, level) => {
    expect(strength(password)).toBe(level);
  });
});

describe("ago", () => {
  it("says how long ago", () => {
    const now = Date.parse("2026-10-30T12:00:00Z");
    expect(ago("2026-10-30T11:59:30Z", now)).toMatch(/now|second/);
    expect(ago(null, now)).toBe("-");
  });
});
