// 에이전트 결과 페이지 — 블록 명세(stats·table·cards·text·callout·list·sources)를 shadcn/ui로 그린다.
// 모델이 만든 건 데이터뿐이고 화면은 이 코드가 정한다. 글자는 전부 React 텍스트로 들어간다(HTML 해석 없음).
import { useMemo, useState } from 'react';
import { ArrowDownUp, ArrowUpRight, CheckCircle2, Copy, Info, Link2, Printer, Search, Sparkles, TriangleAlert } from 'lucide-react';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/ui/card.jsx';
import { Badge } from '@/ui/badge.jsx';
import { Button } from '@/ui/button.jsx';
import { Alert, AlertDescription, AlertTitle } from '@/ui/alert.jsx';
import { Table, TableBody, TableCaption, TableCell, TableHead, TableHeader, TableRow } from '@/ui/table.jsx';
import { Input } from '@/ui/input.jsx';

const httpUrl = (u) => { try { const x = new URL(u); return /^https?:$/.test(x.protocol) ? x.toString() : null; } catch { return null; } };
const host = (u) => { try { return new URL(u).host.replace(/^www\./, ''); } catch { return u; } };

function A({ href, children, className }) {
  const u = httpUrl(href);
  if (!u) return <span className={className}>{children}</span>;
  return <a href={u} target="_blank" rel="noreferrer" className={className ?? 'font-medium underline underline-offset-4 decoration-muted-foreground/40 hover:decoration-foreground'}>{children}</a>;
}

// **굵게**, `코드`, [글](url), 맨 URL
function Inline({ text }) {
  return String(text).split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\(https?:\/\/[^)\s]+\)|https?:\/\/[^\s)]+)/).map((p, i) => {
    if (!p) return null;
    if (/^\*\*.+\*\*$/.test(p)) return <strong key={i} className="font-semibold">{p.slice(2, -2)}</strong>;
    if (/^`.+`$/.test(p)) return <code key={i} className="bg-muted rounded px-1.5 py-0.5 font-mono text-[0.85em]">{p.slice(1, -1)}</code>;
    const m = p.match(/^\[([^\]]+)\]\((.+)\)$/);
    if (m) return <A key={i} href={m[2]}>{m[1]}</A>;
    if (/^https?:\/\//.test(p)) return <A key={i} href={p}>{host(p)}</A>;
    return p;
  });
}

function Md({ src }) {
  const out = [];
  let list = null;
  src.split('\n').forEach((line, i) => {
    const li = line.match(/^\s*(?:[-*]|\d+\.) (.*)$/);
    if (li) { (list ??= []).push(<li key={i}><Inline text={li[1]} /></li>); return; }
    if (list) { out.push(<ul key={`u${i}`} className="my-2 ml-5 list-disc space-y-1 marker:text-muted-foreground">{list}</ul>); list = null; }
    const h = line.match(/^#{1,4} (.*)$/);
    if (h) out.push(<h3 key={i} className="mt-4 text-base font-semibold tracking-tight">{h[1]}</h3>);
    else if (line.trim()) out.push(<p key={i} className="leading-7 [&:not(:first-child)]:mt-2"><Inline text={line} /></p>);
  });
  if (list) out.push(<ul key="ul-end" className="my-2 ml-5 list-disc space-y-1 marker:text-muted-foreground">{list}</ul>);
  return <div className="text-[15px]">{out}</div>;
}

function Stats({ items }) {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      {items.map((s, i) => (
        <Card key={i} className="gap-2 py-5">
          <CardHeader className="px-5">
            <CardDescription>{s.label}</CardDescription>
            <CardTitle className="text-2xl font-semibold tabular-nums tracking-tight">{s.value}</CardTitle>
          </CardHeader>
          {s.hint && <CardContent className="px-5 text-xs text-muted-foreground">{s.hint}</CardContent>}
        </Card>
      ))}
    </div>
  );
}

function DataTable({ columns, rows, caption }) {
  const [q, setQ] = useState('');
  const [sort, setSort] = useState(null);           // { col, dir }
  const view = useMemo(() => {
    let r = rows.filter((row) => !q || row.some((c) => c.toLowerCase().includes(q.toLowerCase())));
    if (sort) {
      const num = (v) => { const n = parseFloat(v.replace(/[^\d.-]/g, '')); return Number.isNaN(n) ? null : n; };
      r = [...r].sort((a, b) => {
        const x = a[sort.col] ?? '', y = b[sort.col] ?? '';
        const nx = num(x), ny = num(y);
        const c = nx !== null && ny !== null ? nx - ny : x.localeCompare(y, 'ko');
        return sort.dir * c;
      });
    }
    return r;
  }, [rows, q, sort]);
  return (
    <Card className="gap-0 py-0">
      {rows.length > 6 && (
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <div className="relative w-64">
            <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-4 -translate-y-1/2" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="표에서 찾기" className="h-8 pl-8" />
          </div>
          <span className="text-muted-foreground text-xs">{view.length} / {rows.length}행</span>
        </div>
      )}
      <Table>
        <TableHeader className="bg-muted/50">
          <TableRow>
            {columns.map((c, i) => (
              <TableHead key={i} className="px-4">
                <button onClick={() => setSort((s) => (s?.col === i ? (s.dir === 1 ? { col: i, dir: -1 } : null) : { col: i, dir: 1 }))}
                        className="hover:text-foreground inline-flex items-center gap-1">
                  {c}<ArrowDownUp className={`size-3 ${sort?.col === i ? 'opacity-100' : 'opacity-30'}`} />
                </button>
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {view.map((r, i) => (
            <TableRow key={i}>
              {columns.map((_, j) => (
                <TableCell key={j} className={`px-4 py-3 align-top whitespace-normal ${j === 0 ? 'font-medium' : ''}`}>
                  <Inline text={r[j] ?? ''} />
                </TableCell>
              ))}
            </TableRow>
          ))}
        </TableBody>
        {caption && <TableCaption className="mb-4">{caption}</TableCaption>}
      </Table>
    </Card>
  );
}

function Cards({ items }) {
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {items.map((c, i) => (
        <Card key={i} className="gap-4">
          <CardHeader>
            <CardTitle className="text-base">{c.title}</CardTitle>
            {c.subtitle && <CardDescription>{c.subtitle}</CardDescription>}
          </CardHeader>
          {(c.body || c.tags?.length) && (
            <CardContent className="flex flex-col gap-3 text-sm">
              {c.body && <p className="leading-6 text-muted-foreground"><Inline text={c.body} /></p>}
              {c.tags?.length > 0 && <div className="flex flex-wrap gap-1.5">{c.tags.map((t, j) => <Badge key={j} variant="secondary">{t}</Badge>)}</div>}
            </CardContent>
          )}
          {httpUrl(c.url) && (
            <CardFooter className="mt-auto">
              <Button variant="outline" size="sm" href={httpUrl(c.url)} target="_blank" rel="noreferrer">
                {host(c.url)}<ArrowUpRight />
              </Button>
            </CardFooter>
          )}
        </Card>
      ))}
    </div>
  );
}

const TONE = {
  info: { Icon: Info, cls: '' },
  warn: { Icon: TriangleAlert, cls: 'border-amber-300/60 bg-amber-50 text-amber-900 dark:bg-amber-950/40 dark:text-amber-200 [&_[data-slot=alert-description]]:text-amber-800/90 dark:[&_[data-slot=alert-description]]:text-amber-200/80' },
  success: { Icon: CheckCircle2, cls: 'border-emerald-300/60 bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200 [&_[data-slot=alert-description]]:text-emerald-800/90 dark:[&_[data-slot=alert-description]]:text-emerald-200/80' },
};
function Callout({ tone, title, body }) {
  const { Icon, cls } = TONE[tone] || TONE.info;
  return (
    <Alert className={cls}>
      <Icon />
      <AlertTitle className="line-clamp-none">{title}</AlertTitle>
      {body && <AlertDescription><p><Inline text={body} /></p></AlertDescription>}
    </Alert>
  );
}

function List({ title, items }) {
  return (
    <Card className="gap-3">
      {title && <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>}
      <CardContent>
        <ul className="ml-5 list-disc space-y-1.5 text-[15px] marker:text-muted-foreground">
          {items.map((t, i) => <li key={i}><Inline text={t} /></li>)}
        </ul>
      </CardContent>
    </Card>
  );
}

function Sources({ items }) {
  return (
    <Card className="gap-3">
      <CardHeader><CardTitle className="flex items-center gap-2 text-base"><Link2 className="size-4" />출처</CardTitle></CardHeader>
      <CardContent className="grid gap-1">
        {items.map((s, i) => (
          <A key={i} href={s.url} className="hover:bg-muted flex items-center gap-3 rounded-md px-2 py-1.5 text-sm">
            <span className="bg-muted text-muted-foreground grid size-6 shrink-0 place-items-center rounded text-xs tabular-nums">{i + 1}</span>
            <span className="min-w-0 flex-1 truncate">{s.title}</span>
            <span className="text-muted-foreground shrink-0 text-xs">{host(s.url)}</span>
          </A>
        ))}
      </CardContent>
    </Card>
  );
}

function Block({ b }) {
  switch (b.type) {
    case 'stats': return <Stats items={b.items} />;
    case 'table': return <DataTable columns={b.columns} rows={b.rows} caption={b.caption} />;
    case 'cards': return <Cards items={b.items} />;
    case 'text': return <Card><CardContent><Md src={b.markdown} /></CardContent></Card>;
    case 'callout': return <Callout {...b} />;
    case 'list': return <List {...b} />;
    case 'sources': return <Sources items={b.items} />;
    default: return null;
  }
}

// 결과를 팀 채널·문서에 붙여 넣기 좋게 마크다운으로 복사
function toMarkdown(spec) {
  const out = [`# ${spec.title}`, spec.subtitle || ''];
  for (const b of spec.blocks) {
    if (b.type === 'stats') out.push(b.items.map((s) => `- **${s.label}**: ${s.value}${s.hint ? ` (${s.hint})` : ''}`).join('\n'));
    if (b.type === 'table') out.push([`| ${b.columns.join(' | ')} |`, `|${b.columns.map(() => '---').join('|')}|`, ...b.rows.map((r) => `| ${r.join(' | ')} |`)].join('\n'));
    if (b.type === 'cards') out.push(b.items.map((c) => `- **${c.title}**${c.subtitle ? ` — ${c.subtitle}` : ''}${c.url ? ` (${c.url})` : ''}`).join('\n'));
    if (b.type === 'text') out.push(b.markdown);
    if (b.type === 'callout') out.push(`> **${b.title}** ${b.body || ''}`);
    if (b.type === 'list') out.push(`${b.title ? `**${b.title}**\n` : ''}${b.items.map((t) => `- ${t}`).join('\n')}`);
    if (b.type === 'sources') out.push(`출처\n${b.items.map((s) => `- ${s.title}: ${s.url}`).join('\n')}`);
  }
  return out.filter(Boolean).join('\n\n');
}

export default function PageApp({ spec }) {
  const [copied, setCopied] = useState(false);
  if (!spec) {
    return <div className="text-muted-foreground grid h-screen place-items-center text-sm">결과 페이지를 읽지 못했습니다.</div>;
  }
  document.title = spec.title;
  return (
    <div className="min-h-screen">
      <header className="border-b bg-card/70 backdrop-blur supports-[backdrop-filter]:bg-card/60">
        <div className="mx-auto flex max-w-6xl flex-col gap-4 px-8 pt-10 pb-8">
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="gap-1 border-brand/30 bg-brand/10 text-brand"><Sparkles />A2A 에이전트 결과</Badge>
            <span className="text-muted-foreground text-xs">{new Date().toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'short' })}</span>
          </div>
          <div className="flex flex-wrap items-end justify-between gap-4">
            <div className="max-w-3xl">
              <h1 className="text-3xl font-bold tracking-tight">{spec.title}</h1>
              {spec.subtitle && <p className="text-muted-foreground mt-2 text-[15px] leading-7">{spec.subtitle}</p>}
            </div>
            <div className="no-print flex gap-2">
              <Button variant="outline" size="sm" onClick={() => { navigator.clipboard.writeText(toMarkdown(spec)); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>
                {copied ? <CheckCircle2 /> : <Copy />}{copied ? '복사됨' : '마크다운 복사'}
              </Button>
              <Button variant="outline" size="sm" onClick={() => window.print()}><Printer />인쇄 · PDF</Button>
            </div>
          </div>
        </div>
      </header>
      <main className="mx-auto flex max-w-6xl flex-col gap-6 px-8 py-8">
        {spec.blocks.map((b, i) => <Block key={i} b={b} />)}
      </main>
    </div>
  );
}
