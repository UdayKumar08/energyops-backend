import type { Metadata } from "next";
import "@fontsource-variable/schibsted-grotesk";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "EnergyOps", template: "%s · EnergyOps" },
  description: "Energy operations for the sites you look after.",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
