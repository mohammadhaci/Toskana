import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import de from "./de.json";
import en from "./en.json";

const STORAGE_KEY = "toskana.lang";

export function storedLanguage(): "de" | "en" {
  try {
    const value = window.localStorage.getItem(STORAGE_KEY);
    return value === "en" ? "en" : "de";
  } catch {
    return "de";
  }
}

export function persistLanguage(lang: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    // storage unavailable (private mode): language just won't persist
  }
}

void i18n.use(initReactI18next).init({
  resources: {
    de: { translation: de },
    en: { translation: en },
  },
  lng: storedLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
  returnNull: false,
});

export default i18n;
