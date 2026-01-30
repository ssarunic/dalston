import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Layout } from '@/components/Layout'
import { Dashboard } from '@/pages/Dashboard'
import { BatchJobs } from '@/pages/BatchJobs'
import { JobDetail } from '@/pages/JobDetail'
import { RealtimeSessions } from '@/pages/RealtimeSessions'
import { Engines } from '@/pages/Engines'

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
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={basename}>
        <Layout>
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/jobs" element={<BatchJobs />} />
            <Route path="/jobs/:jobId" element={<JobDetail />} />
            <Route path="/realtime" element={<RealtimeSessions />} />
            <Route path="/engines" element={<Engines />} />
          </Routes>
        </Layout>
      </BrowserRouter>
    </QueryClientProvider>
  )
}
