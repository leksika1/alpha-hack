import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { LiveNotifications } from '../components/LiveNotifications';
import { ErrorBoundary } from '../components/ErrorBoundary';

export function BankShell() {
  const location = useLocation();
  return (
    <div className="shell">
      <TopBar />
      <div className="shell__body">
        <Sidebar />
        <main className="shell__main">
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
      <LiveNotifications />
    </div>
  );
}
