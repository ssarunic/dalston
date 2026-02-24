import { createBrowserRouter, RouterProvider, Outlet } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { AuthProvider } from '@/contexts/AuthContext'
import { ProtectedRoute } from '@/components/ProtectedRoute'
import { Layout } from '@/components/Layout'
import { Login } from '@/pages/Login'
import { Dashboard } from '@/pages/Dashboard'
import { BatchJobs } from '@/pages/BatchJobs'
import { JobDetail } from '@/pages/JobDetail'
import { TaskDetail } from '@/pages/TaskDetail'
import { RealtimeSessions } from '@/pages/RealtimeSessions'
import { RealtimeSessionDetail } from '@/pages/RealtimeSessionDetail'
import { Engines } from '@/pages/Engines'
import { ApiKeys } from '@/pages/ApiKeys'
import { Webhooks } from '@/pages/Webhooks'
import { WebhookDetail } from '@/pages/WebhookDetail'
import { AuditLog } from '@/pages/AuditLog'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
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
      <Layout>
        <Outlet />
      </Layout>
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
        { path: '/jobs/:jobId', element: <JobDetail /> },
        { path: '/jobs/:jobId/tasks/:taskId', element: <TaskDetail /> },
        { path: '/realtime', element: <RealtimeSessions /> },
        { path: '/realtime/sessions/:sessionId', element: <RealtimeSessionDetail /> },
        { path: '/engines', element: <Engines /> },
        { path: '/keys', element: <ApiKeys /> },
        { path: '/webhooks', element: <Webhooks /> },
        { path: '/webhooks/:endpointId', element: <WebhookDetail /> },
        { path: '/audit', element: <AuditLog /> },
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
