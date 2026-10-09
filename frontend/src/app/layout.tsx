import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = { title: "OEM Revenue Forecast", description: "AI-assisted OEM revenue forecasting platform" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: `try{if(localStorage.getItem("oem.theme")==="dark")document.documentElement.setAttribute("data-theme","dark")}catch(e){}` }} /></head>
      <body className="min-h-screen bg-surface font-sans text-ink antialiased"><Providers>{children}</Providers></body>
    </html>
  );
}
