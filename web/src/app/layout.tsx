import type { Metadata, Viewport } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Void POS",
  description: "In-person point of sale for Void POS.",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en-AU">
      <body className="min-h-dvh">
        <header className="sticky top-0 z-20 border-b border-border bg-surface/90 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold">
              <span className="grid h-7 w-7 place-items-center rounded-md bg-accent text-sm text-white">
                V
              </span>
              <span>Void POS</span>
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              <Link
                href="/"
                className="rounded-md px-3 py-1.5 font-medium text-muted hover:bg-background hover:text-foreground"
              >
                Order
              </Link>
              <Link
                href="/closeout"
                className="rounded-md px-3 py-1.5 font-medium text-muted hover:bg-background hover:text-foreground"
              >
                Closeout
              </Link>
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-7xl px-4 py-4">{children}</main>
      </body>
    </html>
  );
}
