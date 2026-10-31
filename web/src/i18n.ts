// Every text on screen comes from a translation file (FR-57). English is the
// only one in v1.0; adding Urdu or Arabic later means adding one more file.
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./locales/en.json";

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en } },
  lng: "en",
  fallbackLng: "en",
  interpolation: { escapeValue: false }, // React already escapes text
});

export default i18n;
