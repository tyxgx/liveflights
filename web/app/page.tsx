"use client";

import { Nav } from "@/components/landing/Nav";
import { Hero } from "@/components/landing/Hero";
import { LiveTicker } from "@/components/landing/LiveTicker";
import { Pipeline } from "@/components/landing/Pipeline";
import { TechStack } from "@/components/landing/TechStack";
import { EngineeringNotes } from "@/components/landing/EngineeringNotes";
import { Features } from "@/components/landing/Features";
import { Footer } from "@/components/landing/Footer";

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-base">
      <Nav />
      <Hero />
      <LiveTicker />
      <Pipeline />
      <Features />
      <TechStack />
      <EngineeringNotes />
      <Footer />
    </main>
  );
}
