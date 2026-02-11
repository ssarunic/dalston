import { BrowserRouter, Routes, Route } from 'react-router-dom'
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

export default function App() {
  return (
    <AuthProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter basename={basename}>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/*"
              element={
                <ProtectedRoute>
                  <Layout>
                    <Routes>
                      <Route path="/" element={<Dashboard />} />
                      <Route path="/jobs" element={<BatchJobs />} />
                      <Route path="/jobs/:jobId" element={<JobDetail />} />
                      <Route path="/jobs/:jobId/tasks/:taskId" element={<TaskDetail />} />
                      <Route path="/realtime" element={<RealtimeSessions />} />
                      <Route path="/realtime/sessions/:sessionId" element={<RealtimeSessionDetail />} />
                      <Route path="/engines" element={<Engines />} />
                      <Route path="/keys" element={<ApiKeys />} />
                      <Route path="/webhooks" element={<Webhooks />} />
                      <Route path="/webhooks/:endpointId" element={<WebhookDetail />} />
                    </Routes>
                  </Layout>
                </ProtectedRoute>
              }
            />
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </AuthProvider>
  )
}
