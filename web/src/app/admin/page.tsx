"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

// The shell only renders this once the admin is signed in, so redirect on the client.
export default function AdminHome() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/admin/menu");
  }, [router]);
  return <p className="text-sm text-muted">Opening the menu…</p>;
}
