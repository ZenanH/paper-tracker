import type { CSSProperties } from 'react';
export type IconName = 'inbox' | 'star' | 'journals' | 'settings' | 'plus' | 'arrow' | 'sync' | 'check' | 'close' | 'archive' | 'search' | 'external' | 'chevron' | 'book';
const paths: Record<IconName, React.ReactNode> = {
  inbox: <><path d="M4 4h16l2 11v5H2v-5L4 4Z"/><path d="M2 15h6l2 3h4l2-3h6"/></>,
  star: <path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z"/>,
  journals: <><path d="M4 3h14v18H4a2 2 0 0 1 0-4h14M4 3a2 2 0 0 0-2 2v14M7 7h7M7 11h5"/><path d="M21 5v14"/></>,
  settings: <><path d="m9 3-.6 2.3-2 .9-2-.7-2 3.5L4 10.7v2.6l-1.6 1.7 2 3.5 2-.7 2 .9L9 21h4l.6-2.3 2-.9 2 .7 2-3.5-1.6-1.7v-2.6L19.6 9l-2-3.5-2 .7-2-.9L13 3H9Z"/><circle cx="11" cy="12" r="3"/></>,
  plus: <path d="M12 5v14M5 12h14"/>, arrow: <path d="M5 12h14m-6-6 6 6-6 6"/>,
  sync: <><path d="M20 7a9 9 0 0 0-15-1L2 9m0-6v6h6M4 17a9 9 0 0 0 15 1l3-3m0 6v-6h-6"/></>,
  check: <path d="m5 12 4 4L19 6"/>, close: <path d="m6 6 12 12M18 6 6 18"/>,
  archive: <><path d="M3 3h18v5H3zM5 8v13h14V8M10 12h4"/></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  external: <><path d="M14 3h7v7M21 3 11 13M10 5H4v15h15v-6"/></>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  book: <><path d="M12 6C8 3 4 3 2 4v16c3-1 6-1 10 2 4-3 7-3 10-2V4c-2-1-6-1-10 2Zm0 0v16"/></>
};
export function Icon({ name, size = 18, className, style }: { name: IconName; size?: number; className?: string; style?: CSSProperties }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className} style={style}>{paths[name]}</svg>;
}
// The toolbar and favicon share one vector asset.
export function Mark() { return <img src="/logo.svg" width="32" height="32" alt="" aria-hidden="true"/>; }
