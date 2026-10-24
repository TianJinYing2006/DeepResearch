const ICP_BEIAN = '皖ICP备2026036120号'
const ICP_QUERY_URL = 'https://beian.miit.gov.cn/'

const BASE_CLASS =
  'text-ink-muted underline decoration-rule underline-offset-4 transition hover:text-ink'

export default function BeianLink({ className = '' }: { className?: string }) {
  return (
    <a
      href={ICP_QUERY_URL}
      target="_blank"
      rel="noopener noreferrer"
      data-testid="beian-link"
      className={`${BASE_CLASS} ${className}`.trim()}
    >
      {ICP_BEIAN}
    </a>
  )
}
