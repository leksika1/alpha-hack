// Сессия: токены, текущий пользователь и активная компания (как в шапке «Альфа-Бизнеса»).
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { getTokens, login as apiLogin, onTokensChange, setTokens } from '../api/client';
import { useMe } from '../api/hooks';
import type { CompanyBrief, Me } from '../api/types';

const COMPANY_KEY = 'alfa-future.company';

interface Session {
  isAuthenticated: boolean;
  me: Me | undefined;
  meLoading: boolean;
  isAdmin: boolean;
  company: CompanyBrief | undefined;
  companies: CompanyBrief[];
  setCompanyId: (id: string) => void;
  login: (login: string, password: string) => Promise<void>;
  logout: () => void;
}

const SessionContext = createContext<Session | null>(null);

function readCompanyId(): string | null {
  try {
    return localStorage.getItem(COMPANY_KEY);
  } catch {
    return null;
  }
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [authed, setAuthed] = useState(() => !!getTokens());
  const [companyId, setCompanyIdState] = useState<string | null>(readCompanyId);

  useEffect(() => onTokensChange((t) => setAuthed(!!t)), []);

  const meQuery = useMe(authed);
  const me = authed ? meQuery.data : undefined;
  const companies = useMemo(() => me?.companies ?? [], [me]);

  const company = useMemo(() => {
    if (!companies.length) return undefined;
    return (
      companies.find((c) => c.company_id === companyId) ??
      companies.find((c) => c.is_default) ??
      companies[0]
    );
  }, [companies, companyId]);

  const setCompanyId = useCallback((id: string) => {
    setCompanyIdState(id);
    try {
      localStorage.setItem(COMPANY_KEY, id);
    } catch {
      // не критично
    }
  }, []);

  const login = useCallback(
    async (name: string, password: string) => {
      qc.clear();
      await apiLogin(name, password);
      setCompanyIdState(null);
      try {
        localStorage.removeItem(COMPANY_KEY);
      } catch {
        // не критично
      }
    },
    [qc],
  );

  const logout = useCallback(() => {
    setTokens(null);
    qc.clear();
  }, [qc]);

  const value: Session = {
    isAuthenticated: authed,
    me,
    meLoading: authed && meQuery.isLoading,
    isAdmin: me?.platform_role === 'admin',
    company,
    companies,
    setCompanyId,
    login,
    logout,
  };

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession вне SessionProvider');
  return ctx;
}
