import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Company Brain",
  description: "A workspace for your company’s agents.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
