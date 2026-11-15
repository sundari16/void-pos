// Client-side quick-modifier presets.
//
// The backend models modifiers as real catalog objects (ModifierGroup / Modifier),
// but this slice of the API exposes no endpoint to *read* which modifiers apply to an
// item. Until one exists, the POS offers these generic presets on every item; each
// selection becomes a modifier child line (name snapshot + price delta, no catalog id),
// exactly the shape the order pipeline accepts. Prices are integer cents.

export interface QuickModifier {
  key: string;
  name: string;
  kitchenName?: string;
  priceDeltaCents: number;
}

export interface QuickModifierGroup {
  key: string;
  name: string;
  modifiers: QuickModifier[];
}

export const QUICK_MODIFIER_GROUPS: QuickModifierGroup[] = [
  {
    key: "spice",
    name: "Spice",
    modifiers: [
      { key: "mild", name: "Mild", priceDeltaCents: 0 },
      { key: "medium", name: "Medium", priceDeltaCents: 0 },
      { key: "hot", name: "Hot", priceDeltaCents: 0 },
      { key: "extra-hot", name: "Extra hot", priceDeltaCents: 0 },
    ],
  },
  {
    key: "extras",
    name: "Extras",
    modifiers: [
      { key: "extra-cheese", name: "Extra cheese", priceDeltaCents: 150 },
      { key: "extra-sauce", name: "Extra sauce", priceDeltaCents: 100 },
      { key: "add-egg", name: "Add egg", priceDeltaCents: 200 },
    ],
  },
  {
    key: "prep",
    name: "Prep notes",
    modifiers: [
      { key: "no-onion", name: "No onion", priceDeltaCents: 0 },
      { key: "gluten-free", name: "Gluten free", priceDeltaCents: 0 },
    ],
  },
];
