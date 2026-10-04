import { useCallback, useEffect, useState } from 'react';
import { EN, IT, type MessageKey } from './messages';

/**
 * Curate's own en/it catalog, following Mission Control's language.
 *
 * The plugin ships its own strings instead of adding keys to the host catalogs.
 * It follows the host's persisted locale and re-renders when the host's
 * I18nProvider flips `<html lang>`.
 */

export type Locale = 'en' | 'it';
export type Params = Record<string, string | number>;

const CATALOGS: Record<Locale, Record<MessageKey, string>> = { en: EN, it: IT };
const HOST_STORAGE_KEY = 'mission-control-locale';

// The host persists the choice synchronously in setLocale and mirrors it onto <html lang> in an
// effect, which runs after the plugin's own effects. So storage is the truth; `lang` is the signal.
export function currentLocale(): Locale {
  try {
    const stored = window.localStorage.getItem(HOST_STORAGE_KEY);
    if (stored) return stored === 'it' ? 'it' : 'en';
  } catch {
    // storage blocked: fall back to the attribute
  }
  return typeof document !== 'undefined' && document.documentElement.lang === 'it' ? 'it' : 'en';
}

/** `{name}` placeholders; `<key>_one` is used when `count === 1` and the catalog has it. */
export function translate(locale: Locale, key: MessageKey, params?: Params): string {
  const catalog = CATALOGS[locale];
  const singular = params?.count === 1 ? (catalog as Record<string, string>)[`${key}_one`] : undefined;
  const template = singular ?? catalog[key] ?? EN[key] ?? key;
  return params ? template.replace(/\{(\w+)\}/g, (match, name: string) => (name in params ? String(params[name]) : match)) : template;
}

/** For helpers outside components; components use `useT` so they re-render on a switch. */
export function tr(key: MessageKey, params?: Params): string {
  return translate(currentLocale(), key, params);
}

export function useLocale(): Locale {
  const [locale, setLocale] = useState<Locale>(currentLocale);
  useEffect(() => {
    const sync = () => setLocale(currentLocale());
    const observer = new MutationObserver(sync);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['lang'] });
    window.addEventListener('storage', sync);
    sync();
    return () => { observer.disconnect(); window.removeEventListener('storage', sync); };
  }, []);
  return locale;
}

export type Translate = (key: MessageKey, params?: Params) => string;

export function useT(): Translate {
  const locale = useLocale();
  return useCallback((key: MessageKey, params?: Params) => translate(locale, key, params), [locale]);
}

export function formatDate(value: string | undefined, locale: Locale = currentLocale()): string {
  if (!value || value === 'null') return translate(locale, 'common.unknownDate');
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale === 'it' ? 'it-IT' : 'en-US', { dateStyle: 'medium' }).format(date);
}

export function formatDateTime(value: string | undefined, locale: Locale = currentLocale()): string {
  if (!value || value === 'null') return translate(locale, 'common.unknownDate');
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat(locale === 'it' ? 'it-IT' : 'en-US', {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(date);
}
