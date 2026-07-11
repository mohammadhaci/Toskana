import { describe, expect, it } from "vitest";

import { categoryLabel, itemLabel } from "./format";

const drink = { key: "drink", name_de: "Getränk", name_en: "Drink" };

describe("categoryLabel", () => {
  it("localizes de/en with fallbacks", () => {
    expect(categoryLabel(drink, "de")).toBe("Getränk");
    expect(categoryLabel(drink, "de-AT")).toBe("Getränk");
    expect(categoryLabel(drink, "en")).toBe("Drink");
  });
});

describe("itemLabel (Phase-2 display fallback)", () => {
  it("prefers the menu item name when present", () => {
    expect(itemLabel({ menu_item_name: "Aperol Spritz" }, drink, "de")).toBe("Aperol Spritz");
    expect(itemLabel({ menu_item_name: "Aperol Spritz" }, null, "en")).toBe("Aperol Spritz");
  });

  it("falls back to the localized category name without an item", () => {
    expect(itemLabel({ menu_item_name: null }, drink, "de")).toBe("Getränk");
    expect(itemLabel({}, drink, "en")).toBe("Drink");
  });

  it("empty item names never mask the category", () => {
    expect(itemLabel({ menu_item_name: "" }, drink, "en")).toBe("Drink");
  });

  it("uses the fallback when neither item nor category is known", () => {
    expect(itemLabel({ menu_item_name: null }, null, "de", "Unbekannt")).toBe("Unbekannt");
    expect(itemLabel({}, null, "en")).toBe("?");
  });
});
