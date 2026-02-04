import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react'
import { setApiKey as setClientApiKey, apiClient } from '@/api/client'

const API_KEY_STORAGE_KEY = 'dalston_api_key'

interface AuthContextType {
  apiKey: string | null
  isAuthenticated: boolean
  isLoading: boolean
  login: (apiKey: string) => Promise<{ success: boolean; error?: string }>
  logout: () => void
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKey] = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Load API key from sessionStorage on mount
  useEffect(() => {
    const storedKey = sessionStorage.getItem(API_KEY_STORAGE_KEY)
    if (storedKey) {
      setApiKey(storedKey)
      setClientApiKey(storedKey)
    }
    setIsLoading(false)
  }, [])

  const login = useCallback(async (key: string): Promise<{ success: boolean; error?: string }> => {
    // Validate the key
    const result = await apiClient.validateKey(key)

    if (!result.valid) {
      return { success: false, error: 'Invalid API key' }
    }

    if (!result.isAdmin) {
      return { success: false, error: 'API key does not have admin scope' }
    }

    // Store key and update state
    sessionStorage.setItem(API_KEY_STORAGE_KEY, key)
    setApiKey(key)
    setClientApiKey(key)
    return { success: true }
  }, [])

  const logout = useCallback(() => {
    sessionStorage.removeItem(API_KEY_STORAGE_KEY)
    setApiKey(null)
    setClientApiKey(null)
  }, [])

  const value: AuthContextType = {
    apiKey,
    isAuthenticated: apiKey !== null,
    isLoading,
    login,
    logout,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
