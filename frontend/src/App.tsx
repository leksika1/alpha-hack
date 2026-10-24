import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';
import { useSession } from './auth/session';
import { BankShell } from './layout/BankShell';
import { LoginPage } from './pages/LoginPage';
import { HomePage } from './pages/HomePage';
import { AdminPage } from './pages/AdminPage';
import { AnalyticsLayout } from './pages/analytics/AnalyticsLayout';
import { FeedPage } from './pages/analytics/FeedPage';
import { InsightPage } from './pages/analytics/InsightPage';
import { AskPage } from './pages/analytics/AskPage';
import { ProfilePage } from './pages/analytics/ProfilePage';
import { MaterialsPage } from './pages/analytics/MaterialsPage';
import { RequestsPage } from './pages/analytics/RequestsPage';

function RequireAuth({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useSession();
  const location = useLocation();
  if (!isAuthenticated) return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <>{children}</>;
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <BankShell />
          </RequireAuth>
        }
      >
        <Route index element={<HomePage />} />
        <Route path="admin" element={<AdminPage />} />
        <Route path="analytics" element={<AnalyticsLayout />}>
          <Route index element={<FeedPage />} />
          <Route path="digest" element={<Navigate to="/analytics" replace />} />
          <Route path="ask" element={<AskPage />} />
          <Route path="profile" element={<ProfilePage />} />
          <Route path="materials" element={<MaterialsPage />} />
          <Route path="requests" element={<RequestsPage />} />
        </Route>
        <Route path="analytics/insight/:id" element={<InsightPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
