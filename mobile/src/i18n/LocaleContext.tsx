/**
 * LocaleContext — React Context for PAR-014 language switching.
 *
 * Provides { locale, setLocale } to the component tree.
 * Initial locale is read from SecureStore (defaults to 'id-ID').
 * Changes are persisted immediately.
 */
import React, { createContext, useContext, useEffect, useState } from 'react';
import { getLocale, setLocale as persistLocale } from '../services/storage';
import type { NotificationLocale } from '../types/index';

interface LocaleContextValue {
  locale: NotificationLocale;
  setLocale: (locale: NotificationLocale) => Promise<void>;
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: 'id-ID',
  setLocale: async () => {},
});

export const LocaleProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [locale, setLocaleState] = useState<NotificationLocale>('id-ID');

  useEffect(() => {
    // Load persisted locale on mount
    getLocale().then((saved) => setLocaleState(saved));
  }, []);

  const setLocale = async (newLocale: NotificationLocale): Promise<void> => {
    setLocaleState(newLocale);
    await persistLocale(newLocale);
  };

  return (
    <LocaleContext.Provider value={{ locale, setLocale }}>
      {children}
    </LocaleContext.Provider>
  );
};

/**
 * useLocale — hook to access locale context in any component.
 */
export function useLocale(): LocaleContextValue {
  return useContext(LocaleContext);
}
