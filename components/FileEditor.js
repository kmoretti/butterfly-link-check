import { useEffect, useState, useRef, useCallback } from 'react'

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export default function FileEditor({ fileName, content, isLoading, onContentChange, onSave, saveVersion }) {
  const contentRef = useRef(null)
  const backdropRef = useRef(null)
  const [searchTerm, setSearchTerm] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    setDirty(false)
  }, [fileName, saveVersion])

  const handleChange = useCallback((event) => {
    onContentChange(event.target.value)
    setDirty(true)
  }, [onContentChange])

  const scrollToMatch = useCallback((index) => {
    const textarea = contentRef.current
    if (!textarea) return
    const lineHeight = parseFloat(window.getComputedStyle(textarea).lineHeight) || 20
    const lines = content.substring(0, index).split('\n').length
    textarea.scrollTop = Math.max(0, (lines - 1) * lineHeight - textarea.clientHeight * 0.25)
  }, [content])

  const handleScroll = useCallback(() => {
    if (backdropRef.current && contentRef.current) {
      backdropRef.current.scrollTop = contentRef.current.scrollTop
      backdropRef.current.scrollLeft = contentRef.current.scrollLeft
    }
  }, [])

  useEffect(() => {
    if (!searchTerm.trim()) {
      setSearchResults([])
      return
    }
    const regex = new RegExp(escapeRegExp(searchTerm), 'gi')
    const matches = []
    let match
    while ((match = regex.exec(content)) !== null) matches.push(match.index)
    setSearchResults(matches)
    setCurrentMatchIndex(0)
    if (matches.length) scrollToMatch(matches[0])
  }, [searchTerm, content, scrollToMatch])

  const navigateMatch = (direction) => {
    if (!searchResults.length) return
    const next = (currentMatchIndex + direction + searchResults.length) % searchResults.length
    setCurrentMatchIndex(next)
    scrollToMatch(searchResults[next])
  }

  const save = async () => {
    const saved = await onSave()
    if (saved) setDirty(false)
  }

  function escapeHtml(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }

  function highlightContent(text, term, currentIndex) {
    if (!term.trim()) return escapeHtml(text)
    const parts = text.split(new RegExp(`(${escapeRegExp(term)})`, 'gi'))
    let matchCount = 0
    return parts.map((part, index) => {
      if (index % 2 === 1) {
        const className = matchCount++ === currentIndex ? 'highlight-mark current-match' : 'highlight-mark'
        return `<mark class="${className}">${escapeHtml(part)}</mark>`
      }
      return escapeHtml(part)
    }).join('')
  }

  return (
    <main className="main-content">
      <div className="editor-header">
        <div className="editor-title"><h3>编辑: <code>{fileName}</code>{dirty && <span className="unsaved-badge">未保存</span>}</h3></div>
        <div className="editor-actions">
          <div className="search-toolbar">
            <input type="text" placeholder="搜索..." value={searchTerm} onChange={event => setSearchTerm(event.target.value)} className="search-input" aria-label="搜索文件内容" />
            {searchResults.length > 0 && <div className="search-nav">
              <button onClick={() => navigateMatch(-1)} disabled={searchResults.length <= 1} aria-label="上一个匹配">⬆</button>
              <span className="search-count">{currentMatchIndex + 1}/{searchResults.length}</span>
              <button onClick={() => navigateMatch(1)} disabled={searchResults.length <= 1} aria-label="下一个匹配">⬇</button>
            </div>}
          </div>
          <button onClick={save} disabled={isLoading || !dirty} className="save-btn">{isLoading ? '保存中...' : '保存'}</button>
        </div>
      </div>

      {isLoading ? <div className="loading-screen">加载文件中...</div> : (
        <div className="editor-wrapper">
          <pre ref={backdropRef} className="editor-highlighter" dangerouslySetInnerHTML={{ __html: highlightContent(content, searchTerm, currentMatchIndex) }} aria-hidden="true" />
          <textarea ref={contentRef} value={content} onChange={handleChange} onScroll={handleScroll} spellCheck={false} className="code-editor" aria-label={`${fileName} 文件内容`} />
        </div>
      )}
    </main>
  )
}
