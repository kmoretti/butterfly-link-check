import { useState, useCallback, useEffect } from 'react'
import { useAuth } from '../components/AuthProvider'
import LoginForm from '../components/LoginForm'
import DashboardLayout from '../components/DashboardLayout'
import Sidebar from '../components/Sidebar'
import FileEditor from '../components/FileEditor'
import LinkSorter from '../components/LinkSorter'
import LinkDashboard from '../components/LinkDashboard'
import { AVAILABLE_FILES } from '../lib/github'

function getToken() {
  if (typeof window === 'undefined') return null
  return sessionStorage.getItem('auth_token')
}

async function apiFetch(url, options = {}) {
  const token = getToken()
  const response = await fetch(url, {
    ...options,
    headers: { ...options.headers, Authorization: `Bearer ${token}` },
  })
  if (response.status === 401) {
    sessionStorage.removeItem('auth_token')
    window.location.reload()
    throw new Error('未授权')
  }
  const data = await response.json()
  if (!response.ok) {
    const error = new Error(data.error || '请求失败')
    error.status = response.status
    throw error
  }
  return data
}

export default function Admin() {
  const { isAuthenticated, loading, login } = useAuth()
  const [fileContent, setFileContent] = useState('')
  const [fileSha, setFileSha] = useState('')
  const [activeFile, setActiveFile] = useState(null)
  const [fileLoading, setFileLoading] = useState(false)
  const [saveVersion, setSaveVersion] = useState(0)
  const [message, setMessage] = useState(null)

  const loadFile = useCallback(async (fileName = activeFile) => {
    if (!fileName) return false
    setFileLoading(true)
    try {
      const data = await apiFetch(`/api/file?name=${fileName}`)
      setActiveFile(fileName)
      setFileContent(data.content)
      setFileSha(data.sha)
      setSaveVersion(version => version + 1)
      setMessage({ type: 'success', text: '已加载远端文件。' })
      return true
    } catch (error) {
      setMessage({ type: 'error', text: `加载失败：${error.message}` })
      return false
    } finally {
      setFileLoading(false)
    }
  }, [activeFile])

  useEffect(() => {
    if (isAuthenticated && !activeFile) loadFile('link.yml')
  }, [isAuthenticated, activeFile, loadFile])

  const handleFileSelect = useCallback((fileName, content, sha) => {
    setActiveFile(fileName)
    setFileContent(content)
    setFileSha(sha)
    setSaveVersion(version => version + 1)
    setMessage(null)
  }, [])

  const handleSave = useCallback(async () => {
    if (!activeFile) return false
    setFileLoading(true)
    setMessage(null)
    try {
      const data = await apiFetch(`/api/file?name=${activeFile}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: fileContent, sha: fileSha }),
      })
      setFileSha(data.sha)
      setSaveVersion(version => version + 1)
      setMessage({ type: 'success', text: '已保存到 GitHub。' })
      return true
    } catch (error) {
      const conflict = error.status === 409 || /sha|conflict|不是最新/i.test(error.message)
      setMessage({
        type: 'error',
        text: conflict ? '远端文件已变更。当前本地修改已保留，请先重新加载后手动合并。' : `保存失败：${error.message}`,
        reload: conflict,
      })
      return false
    } finally {
      setFileLoading(false)
    }
  }, [activeFile, fileContent, fileSha])

  if (loading) return <div className="loading-screen" style={{ height: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>加载中...</div>
  if (!isAuthenticated) return <LoginForm onLogin={login} />

  return (
    <DashboardLayout>
      {({ activeView, sidebarOpen, toggleSidebar }) => {
        const showSorter = activeView === 'sort' && activeFile === 'link.yml'
        return (
          <>
            <Sidebar files={AVAILABLE_FILES} activeFile={activeFile} onFileSelect={handleFileSelect} isOpen={sidebarOpen} onToggle={toggleSidebar} />
            {message && <div className={`save-message ${message.type}`} role="status">
              <span>{message.text}</span>
              {message.reload && <button onClick={() => loadFile()}>重新加载</button>}
            </div>}
            {activeView === 'dashboard' ? <LinkDashboard /> : showSorter ? (
              <LinkSorter content={fileContent} onChange={setFileContent} onSave={handleSave} isLoading={fileLoading} />
            ) : (
              <FileEditor fileName={activeFile} content={fileContent} isLoading={fileLoading} onContentChange={setFileContent} onSave={handleSave} saveVersion={saveVersion} />
            )}
          </>
        )
      }}
    </DashboardLayout>
  )
}
