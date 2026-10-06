import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import ErrorBoundary from './components/ErrorBoundary'
import SharedReportPage from './components/SharedReportPage'
import './index.css'

// 需求 26：/s/<token> 免登录只读分享页（优先于主应用；数据走 /api/share/<token>）
const shareMatch = window.location.pathname.match(/^\/s\/([A-Za-z0-9_-]{10,128})$/)

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ErrorBoundary>
      {shareMatch ? <SharedReportPage token={shareMatch[1]} /> : <App />}
    </ErrorBoundary>
  </React.StrictMode>,
)
