import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Workspace } from "@/components/workspace";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Overview · ReconcileIQ", template: "%s · ReconcileIQ" },
  description:
    "A local, auditable accounts payable workspace. Milestone 01 synthetic demo.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
  colorScheme: "light",
  themeColor: "#ffffff",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Workspace>{children}</Workspace>
      </body>
    </html>
  );
}
