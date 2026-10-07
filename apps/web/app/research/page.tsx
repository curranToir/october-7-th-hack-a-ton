import type { Metadata } from "next";
import ResearchWorkspace from "./workspace";

export const metadata: Metadata = {
  title: "Research · Toir",
  description: "Find prospective clients and review the evidence behind each opportunity.",
};

export default function ResearchPage() { return <ResearchWorkspace />; }
