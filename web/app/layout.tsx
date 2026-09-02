import type { Metadata } from "next";
import { IBM_Plex_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";

// IBM Plex Sans over Inter: Inter is the single most common "AI-generated
// site" typeface fingerprint. Plex has real engineering-instrument
// character (IBM designed it for technical documentation) and pairs
// naturally with JetBrains Mono for the data-heavy dashboard side.
const plexSans = IBM_Plex_Sans({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-plex",
  display: "swap",
});
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jetbrains",
  display: "swap",
});

const title = "liveflights — Real-Time Flight Intelligence";
const description =
  "A live AWS pipeline tracking real ADS-B transponder data across Europe — interactive map, route corridors, and dashboards computed on the fly, not canned.";

export const metadata: Metadata = {
  title,
  description,
  openGraph: {
    title,
    description,
    type: "website",
    siteName: "liveflights",
  },
  twitter: {
    card: "summary",
    title,
    description,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexSans.variable} ${jetbrainsMono.variable}`}>
      <body className="font-sans antialiased">{children}</body>
    </html>
  );
}
