import type { Metadata } from "next";
import "./globals.css";
import "./catalyst.css";

export const metadata: Metadata = {
  title: "CATALYST | Catalysis data workspace",
  description:
    "Upload catalysis data, review standardized revisions, and publish approved records to SciSure.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
