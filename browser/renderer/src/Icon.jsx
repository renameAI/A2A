// 선 아이콘(24 그리드, currentColor)
const P = {
  back: <path d="M15 18l-6-6 6-6" />,
  fwd: <path d="M9 18l6-6-6-6" />,
  reload: <><path d="M20 12a8 8 0 1 1-2.34-5.66" /><path d="M20 4v5h-5" /></>,
  star: <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z" />,
  spark: <><path d="M11 3.5c.5 3.9 1.7 5.1 5.5 5.5-3.8.5-5 1.7-5.5 5.5-.5-3.8-1.7-5-5.5-5.5 3.8-.4 5-1.6 5.5-5.5z" /><path d="M18 14.5c.25 1.7.8 2.25 2.5 2.5-1.7.25-2.25.8-2.5 2.5-.25-1.7-.8-2.25-2.5-2.5 1.7-.25 2.25-.8 2.5-2.5z" /></>,
  chat: <path d="M20 11.5a7.5 7.5 0 0 1-10.9 6.7L4.5 19.5l1.3-4.3A7.5 7.5 0 1 1 20 11.5z" />,
  plus: <path d="M12 5.5v13M5.5 12h13" />,
  x: <path d="M7 7l10 10M17 7L7 17" />,
  sidebar: <><rect x="3.5" y="4.5" width="17" height="15" rx="3" /><path d="M9.5 4.5v15" /></>,
  compose: <><path d="M11.5 4.5H7a2.5 2.5 0 0 0-2.5 2.5v10A2.5 2.5 0 0 0 7 19.5h10a2.5 2.5 0 0 0 2.5-2.5v-4.5" /><path d="M17.3 4.2a1.9 1.9 0 0 1 2.7 2.7L13 13.8l-3.3.6.6-3.3z" /></>,
  globe: <><circle cx="12" cy="12" r="8.5" /><path d="M3.5 12h17M12 3.5c2.3 2.4 3.4 5.2 3.4 8.5s-1.1 6.1-3.4 8.5c-2.3-2.4-3.4-5.2-3.4-8.5s1.1-6.1 3.4-8.5z" /></>,
  hash: <path d="M5 9.5h14M5 14.5h14M10.5 4.5l-2 15M15.5 4.5l-2 15" />,
  lock: <><rect x="5.5" y="10.5" width="13" height="9.5" rx="2.2" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></>,
  palette: <><path d="M12 3.5a8.5 8.5 0 1 0 0 17c1 0 1.6-.8 1.6-1.6 0-.5-.2-.8-.4-1.1-.3-.3-.4-.7-.4-1.1 0-.9.7-1.6 1.6-1.6h1.9a4.2 4.2 0 0 0 4.2-4.2c0-4.1-3.8-7.4-8.5-7.4z" /><circle cx="7.6" cy="11.2" r="1" /><circle cx="10" cy="7.4" r="1" /><circle cx="14.6" cy="7.6" r="1" /></>,
  arrowUp: <path d="M12 18.5v-13M6.5 11L12 5.5l5.5 5.5" />,
  stop: <rect x="7" y="7" width="10" height="10" rx="2.2" />,
  check: <path d="M5.5 12.5l4.2 4.2 8.8-9.4" />,
  alert: <><circle cx="12" cy="12" r="8.5" /><path d="M12 8v4.6M12 15.8h.01" /></>,
  search: <><circle cx="11" cy="11" r="6.5" /><path d="M19.5 19.5l-3.8-3.8" /></>,
  page: <><path d="M13.5 3.5H7.5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V8.5z" /><path d="M13.5 3.5v5h5M9 13h6M9 16.5h4" /></>,
  cursor: <path d="M6 4.5l12 6.2-5.3 1.7-1.8 5.4z" />,
  keyboard: <><rect x="3.5" y="6.5" width="17" height="11" rx="2.2" /><path d="M7.5 10.5h.01M11 10.5h.01M14.5 10.5h.01M8 14h8" /></>,
  tabs: <><rect x="3.5" y="5.5" width="17" height="13" rx="2.2" /><path d="M3.5 9.5h17M9 5.5v4" /></>,
  image: <><rect x="3.5" y="4.5" width="17" height="15" rx="2.2" /><circle cx="9" cy="10" r="1.8" /><path d="M20.5 16l-4.8-4.8-8.7 8.3" /></>,
  shield: <><path d="M12 3.5l7 2.8v5.4c0 4.2-2.9 7.7-7 8.8-4.1-1.1-7-4.6-7-8.8V6.3z" /><path d="M9 12l2.1 2.1L15.2 10" /></>,
  scroll: <path d="M12 5v14M7.5 14.5L12 19l4.5-4.5" />,
  list: <path d="M8.5 7h11M8.5 12h11M8.5 17h11M4.5 7h.01M4.5 12h.01M4.5 17h.01" />,
  copy: <><rect x="8.5" y="8.5" width="11" height="11" rx="2.2" /><path d="M15.5 8.5V6.5a2 2 0 0 0-2-2h-7a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h2" /></>,
  download: <><path d="M12 4.5v11M7.5 11l4.5 4.5 4.5-4.5" /><path d="M5 19.5h14" /></>,
  folder: <path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z" />,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7.5V12l3 2" /></>,
  key: <><circle cx="8" cy="15.5" r="3.5" /><path d="M10.5 13l7.5-7.5M16 7.5l2 2M14 9.5l1.5 1.5" /></>,
  eye: <><path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z" /><circle cx="12" cy="12" r="2.8" /></>,
  trash: <><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 12.5h9l1-12.5" /></>,
  home: <path d="M4.5 10.8L12 4.5l7.5 6.3V19a1 1 0 0 1-1 1h-4.3v-5.2H9.8V20H5.5a1 1 0 0 1-1-1z" />,
  person: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 19.5c.8-3.4 3.6-5.5 7-5.5s6.2 2.1 7 5.5" /></>,
  people: <><circle cx="9" cy="9" r="3" /><path d="M3.5 19c.6-3 2.8-4.8 5.5-4.8s4.9 1.8 5.5 4.8" /><circle cx="16.5" cy="8" r="2.5" /><path d="M15.5 13.3c2.6-.3 4.6 1.3 5.2 4.2" /></>,
  external: <><path d="M14 4.5h5.5V10" /><path d="M19.5 4.5L11 13" /><path d="M18 13.5V18a1.5 1.5 0 0 1-1.5 1.5h-10A1.5 1.5 0 0 1 5 18V7.5A1.5 1.5 0 0 1 6.5 6H11" /></>,
  smile: <><circle cx="12" cy="12" r="8.5" /><path d="M8.5 14c.9 1.3 2.1 2 3.5 2s2.6-.7 3.5-2M9 9.5h.01M15 9.5h.01" /></>,
  reply: <path d="M9.5 7.5L5 12l4.5 4.5M5.5 12h8a5.5 5.5 0 0 1 5.5 5.5v1" />,
  chevronDown: <path d="M6.5 9.5l5.5 5.5 5.5-5.5" />,
  share: <><path d="M12 15.5V4.5M7.5 9L12 4.5 16.5 9" /><path d="M5 13.5v4A2 2 0 0 0 7 19.5h10a2 2 0 0 0 2-2v-4" /></>,
  logo: <><circle cx="9" cy="12" r="5.5" /><circle cx="15" cy="12" r="5.5" /></>,
  graph: <><circle cx="6" cy="7" r="2.2" /><circle cx="18" cy="6" r="2.2" /><circle cx="12" cy="17.5" r="2.4" /><path d="M8 8.2l2.7 7.3M16.3 7.7l-3.2 7.8M8.2 6.8l7.6-.6" /></>,
  flask: <path d="M9.5 4.5h5M10.5 4.5v5L5.8 17.3A1.5 1.5 0 0 0 7.1 19.5h9.8a1.5 1.5 0 0 0 1.3-2.2L13.5 9.5v-5M8 14h8" />,
};

export default function Icon({ name, className = 'size-4', fill = 'none', strokeWidth = 1.7 }) {
  return (
    <svg viewBox="0 0 24 24" fill={fill} stroke="currentColor" strokeWidth={strokeWidth}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={`shrink-0 ${className}`}>
      {P[name]}
    </svg>
  );
}
