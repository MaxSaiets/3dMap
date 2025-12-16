import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "3D Map Generator",
  description: "Генератор 3D моделей міст з OpenStreetMap",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="uk">
      <body>{children}</body>
    </html>
  );
}

