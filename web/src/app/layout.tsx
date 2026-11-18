import type { Metadata, Viewport } from "next";
import { AppHeader } from "@/components/AppHeader";
import { TillProvider } from "@/components/TillProvider";
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
        <TillProvider>
          <AppHeader />
          <main className="mx-auto max-w-7xl px-4 py-4">{children}</main>
        </TillProvider>
      </body>
    </html>
  );
}
