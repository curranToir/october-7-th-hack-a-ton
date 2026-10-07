import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "TOIR",
  description: "A workspace for your company’s agents.",
  icons: { icon: "/brand/toir-mark.png", apple: "/brand/toir-mark.png" },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
