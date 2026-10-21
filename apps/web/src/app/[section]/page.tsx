import { notFound } from "next/navigation";
import { Dashboard, type Section } from "@/components/dashboard";
import { getSnapshot } from "@/lib/api";

export const dynamic = "force-dynamic";

const sections = [
  "invoices",
  "purchase-orders",
  "reconciliation",
  "exceptions",
  "audit-log",
] as const;

export async function generateMetadata({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  return {
    title: section
      .split("-")
      .map((word) => word[0].toUpperCase() + word.slice(1))
      .join(" "),
  };
}

export default async function FinancePage({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;
  if (!(sections as readonly string[]).includes(section)) notFound();
  return (
    <Dashboard snapshot={await getSnapshot()} section={section as Section} />
  );
}
