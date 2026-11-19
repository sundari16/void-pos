"use client";

import { useCallback, useEffect, useState } from "react";
import { useAdminLocation } from "@/components/admin/AdminProvider";
import { ItemEditor } from "@/components/admin/menu/ItemEditor";
import { ItemsList } from "@/components/admin/menu/ItemsList";
import { MenuChanges } from "@/components/admin/menu/MenuChanges";
import { ModifierGroups } from "@/components/admin/menu/ModifierGroups";
import { ErrorNote, PageHeader, primaryButton, smallButton } from "@/components/admin/ui";
import { errorDetail } from "@/lib/adminApi";
import { displayTime } from "@/lib/adminForms";
import { categoriesOf, menuNames } from "@/lib/adminMenu";
import type { MenuChange, TillMenu } from "@/lib/adminTypes";

type Tab = "items" | "groups" | "changes";

export default function MenuPage() {
  const { api, location } = useAdminLocation();
  const [menu, setMenu] = useState<TillMenu | null>(null);
  const [changes, setChanges] = useState<MenuChange[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("items");
  // "new" for the add form, an item id to edit one, null when closed.
  const [editing, setEditing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextMenu, nextChanges] = await Promise.all([
        api.menu(location.id),
        api.menuChanges(location.id, 50),
      ]);
      setMenu(nextMenu);
      setChanges(nextChanges);
      setError(null);
    } catch (err) {
      setError(errorDetail(err));
    }
  }, [api, location.id]);

  useEffect(() => {
    load();
  }, [load]);

  const editingItem = editing && editing !== "new" ? menu?.items.find((i) => i.id === editing) : null;

  const tabs: { id: Tab; label: string }[] = [
    { id: "items", label: `Items${menu ? ` (${menu.items.length})` : ""}` },
    { id: "groups", label: `Modifier groups${menu ? ` (${menu.modifier_groups.length})` : ""}` },
    { id: "changes", label: "Recent changes" },
  ];

  return (
    <div className="space-y-4">
      <PageHeader title="Menu">
        {menu && (
          <span className="text-sm text-muted">
            {displayTime(menu.local_time)} at the venue · prices include GST
          </span>
        )}
        <button onClick={load} className={smallButton}>
          Refresh
        </button>
        <button onClick={() => setEditing("new")} disabled={!menu} className={primaryButton}>
          Add item
        </button>
      </PageHeader>

      <div role="tablist" className="flex gap-1 overflow-x-auto rounded-xl bg-surface p-1 ring-1 ring-border">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            onClick={() => setTab(t.id)}
            className={`flex-1 whitespace-nowrap rounded-lg px-3 py-2 text-sm font-medium ${
              tab === t.id ? "bg-accent text-white" : "text-muted hover:bg-background hover:text-foreground"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {error && <ErrorNote>{error}</ErrorNote>}
      {!menu && !error && <p className="text-sm text-muted">Loading…</p>}

      {menu && tab === "items" && (
        <ItemsList api={api} menu={menu} onEdit={(item) => setEditing(item.id)} onChanged={load} />
      )}
      {menu && tab === "groups" && (
        <ModifierGroups api={api} locationId={location.id} menu={menu} onChanged={load} />
      )}
      {menu && tab === "changes" && (
        <MenuChanges
          api={api}
          changes={changes}
          names={menuNames(menu)}
          timezone={location.timezone}
          onChanged={load}
        />
      )}

      {menu && editing && (editing === "new" || editingItem) && (
        <ItemEditor
          api={api}
          locationId={location.id}
          item={editingItem ?? null}
          categories={categoriesOf(menu.items)}
          groups={menu.modifier_groups}
          onSaved={load}
          onClose={() => setEditing(null)}
        />
      )}
    </div>
  );
}
