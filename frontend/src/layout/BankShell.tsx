import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { LiveNotifications } from '../components/LiveNotifications';
import { ErrorBoundary } from '../components/ErrorBoundary';

export function BankShell() {
  const location = useLocation();
  const section = location.pathname.startsWith('/analytics/insight') ? location.pathname : location.pathname.split('/')[1];
  return (
    <div className="min-h-screen">
      <TopBar />
      <div className="flex items-start">
        <Sidebar />
        <main className="max-w-[1240px] min-w-0 flex-1 px-4 pb-16 md:pr-8 md:pl-4">
          <ErrorBoundary resetKey={location.pathname}>
            <div key={section} className="motion-safe:animate-fade-up">
              <Outlet />
            </div>
          </ErrorBoundary>
        </main>
      </div>
      <LiveNotifications />
    </div>
  );
}
