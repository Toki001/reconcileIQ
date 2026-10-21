import { Dashboard } from "@/components/dashboard";
import { getSnapshot } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function OverviewPage() {
  return <Dashboard snapshot={await getSnapshot()} section="overview" />;
}
