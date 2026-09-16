import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { cloneElement, createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { afterEach, vi } from "vitest";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

// jsdom has no layout engine: give Recharts' responsive container a fixed size.
vi.mock("recharts", async (importOriginal) => {
  const original = await importOriginal<typeof import("recharts")>();
  return {
    ...original,
    ResponsiveContainer: ({ children, height }: { children: ReactNode; height?: number }) =>
      createElement(
        "div",
        { style: { width: 800, height: height ?? 300 } },
        isValidElement(children)
          ? cloneElement(children as ReactElement<{ width: number; height: number }>, {
              width: 800,
              height: typeof height === "number" ? height : 300,
            })
          : children,
      ),
  };
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, "ResizeObserver", { value: ResizeObserverStub, writable: true });

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }),
});

if (!URL.createObjectURL) {
  Object.defineProperty(URL, "createObjectURL", { value: () => "blob:mock", writable: true });
  Object.defineProperty(URL, "revokeObjectURL", { value: () => {}, writable: true });
}
