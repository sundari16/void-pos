import { Storefront } from "@/components/online/Storefront";

// Next passes params as a promise; unwrap it here and hand the id to the client storefront.
export default async function StorefrontPage({
  params,
}: {
  params: Promise<{ locationId: string }>;
}) {
  const { locationId } = await params;
  return <Storefront locationId={locationId} />;
}
