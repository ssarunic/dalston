import { createBrowserRouter, RouterProvider, Outlet } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from '@/contexts/AuthContext'
import { LiveSessionProvider } from '@/contexts/LiveSessionContext'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { Layout } from '@/components/Layout'
import { Login } from '@/pages/Login'
import { Dashboard } from '@/pages/Dashboard'
import { BatchJobs } from '@/pages/BatchJobs'
import { NewJob } from '@/pages/NewJob'
import { JobDetail } from '@/pages/JobDetail'
import { TaskDetail } from '@/pages/TaskDetail'
import { RealtimeSessions } from '@/pages/RealtimeSessions'
import { RealtimeSessionDetail } from '@/pages/RealtimeSessionDetail'
import { RealtimeLive } from '@/pages/RealtimeLive'
import { Engines } from '@/pages/Engines'
import { ApiKeys } from '@/pages/ApiKeys'
import { Webhooks } from '@/pages/Webhooks'
import { WebhookDetail } from '@/pages/WebhookDetail'
import { AuditLog } from '@/pages/AuditLog'
import { Settings } from '@/pages/Settings'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30000, // 30s - prevents skeleton flash on navigation; refetchInterval handles live updates
      refetchOnWindowFocus: true,
    },
  },
})

// Get basename from Vite's base URL (removes trailing slash)
const basename = import.meta.env.BASE_URL.replace(/\/$/, '') || '/'

// Protected layout wrapper
function ProtectedLayout() {
  return (
    <ProtectedRoute>
      <LiveSessionProvider>
        <Layout>
          <Outlet />
        </Layout>
      </LiveSessionProvider>
    </ProtectedRoute>
  )
}

const router = createBrowserRouter(
  [
    { path: '/login', element: <Login /> },
    {
      element: <ProtectedLayout />,
      children: [
        { path: '/', element: <Dashboard /> },
        { path: '/jobs', element: <BatchJobs /> },
        { path: '/jobs/new', element: <NewJob /> },
        { path: '/jobs/:jobId', element: <JobDetail /> },
        { path: '/jobs/:jobId/tasks/:taskId', element: <TaskDetail /> },
        { path: '/realtime', element: <RealtimeSessions /> },
        { path: '/realtime/live', element: <RealtimeLive /> },
        { path: '/realtime/sessions/:sessionId', element: <RealtimeSessionDetail /> },
        { path: '/engines', element: <Engines /> },
        { path: '/keys', element: <ApiKeys /> },
        { path: '/webhooks', element: <Webhooks /> },
        { path: '/webhooks/:endpointId', element: <WebhookDetail /> },
        { path: '/audit', element: <AuditLog /> },
        { path: '/settings', element: <Settings /> },
      ],
    },
  ],
  { basename }
)

export default function App() {
  return (
    <AuthProvider>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </AuthProvider>
  )
}
