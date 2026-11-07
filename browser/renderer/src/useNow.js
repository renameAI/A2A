import { useEffect, useState } from 'react';

// 진행 중인 작업이 있을 때만 1초마다 다시 그려 경과 시간을 갱신한다
export function useNow(active) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [active]);
  return now;
}
