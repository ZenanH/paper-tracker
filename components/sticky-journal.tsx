'use client';
import { useEffect, useRef, type ReactNode } from 'react';

export function StickyJournal({ children }: { children: ReactNode }) {
  const marker = useRef<HTMLDivElement>(null), header = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const observer = new IntersectionObserver(([entry]) => {
      if (header.current) header.current.dataset.stuck = String(!entry.isIntersecting && entry.boundingClientRect.top < 0);
    }, { threshold: 0 });
    observer.observe(marker.current!);
    return () => observer.disconnect();
  }, []);
  return <><div ref={marker} className="journal-sticky-marker" aria-hidden="true"/><div ref={header} className="current-journal">{children}</div></>;
}
