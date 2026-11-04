import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";
import { Shell } from "@/components/Shell";

/** Self-hosted (SIL OFL, licences beside the files): no external font fetch, so the Tower works on an isolated network. */
const publicSans = localFont({
  src: [
    { path: "../fonts/public-sans-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../fonts/public-sans-latin-500-normal.woff2", weight: "500", style: "normal" },
    { path: "../fonts/public-sans-latin-600-normal.woff2", weight: "600", style: "normal" },
    { path: "../fonts/public-sans-latin-700-normal.woff2", weight: "700", style: "normal" },
  ],
  variable: "--font-public-sans",
  display: "swap",
});
const jetbrainsMono = localFont({
  src: [
    { path: "../fonts/jetbrains-mono-latin-400-normal.woff2", weight: "400", style: "normal" },
    { path: "../fonts/jetbrains-mono-latin-500-normal.woff2", weight: "500", style: "normal" },
  ],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "DQ Command Tower",
  description: "Human-in-the-loop decisions for the Medicaid / HHS Data Quality Agent",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${publicSans.variable} ${jetbrainsMono.variable}`}>
      <body>
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
