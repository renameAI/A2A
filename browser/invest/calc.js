// 투자심사 계산 — 모델은 숫자를 만들지 않는다(PAL/PoT, FinSight의 "변수 메모리").
//
// 잠긴 가정(pack)에서 모든 파생 숫자를 이름 붙은 변수로 계산하고, 글은 {{var:이름}}으로만 숫자를 부른다.
// 같은 변수를 모든 섹션이 쓰므로 요약과 본문 숫자가 어긋날 수 없다. 검산(checks)은 사람이 쓴 표끼리의 모순도 잡는다.
//
// pack 모양(금액은 원, 비율은 0~1):
//   deal:   { amount, price, shares, preShares, postShares, investDate, exitDate, exitShares? }
//   pnl:    { company: { [year]: { revenue, cogs, sga, op, ni } }, reviewer: { ... } }   (없어도 됨)
//   valuation: { exitYear, basis: 'reviewer'|'company', perBand: [min, base, max] | null,
//                peers: [{ name, per, excluded?, reason? }], discount: [lo, hi] | null }
const DAY = 86400000;

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const round = (v, d = 0) => (v == null ? null : Math.round(v * 10 ** d) / 10 ** d);

function years(a, b) {
  const x = Date.parse(a), y = Date.parse(b);
  return Number.isFinite(x) && Number.isFinite(y) && y > x ? (y - x) / DAY / 365 : null;
}

// 변수 하나 = { value, unit, formula, inputs } — 글에서 부를 때 단위에 맞춰 찍는다
function compute(pack = {}) {
  const v = {};
  const checks = [];
  const put = (name, value, unit, formula, inputs = []) => { if (value != null) v[name] = { value, unit, formula, inputs }; return value; };
  const check = (id, status, message) => checks.push({ id, status, message });
  const d = pack.deal || {};

  const amount = num(d.amount), price = num(d.price), shares = num(d.shares);
  put('invest_amount', amount, 'KRW', '입력', ['deal.amount']);
  put('price_per_share', price, 'KRW', '입력', ['deal.price']);
  put('shares_bought', shares, 'shares', '입력', ['deal.shares']);
  if (price != null && shares != null) {
    const implied = put('amount_implied', price * shares, 'KRW', 'price × shares', ['price_per_share', 'shares_bought']);
    if (amount != null) {
      const gap = Math.abs(implied - amount) / amount;
      check('amount_math', gap < 0.001 ? 'pass' : gap < 0.01 ? 'warn' : 'fail',
        `투자단가 × 주식수 = ${implied.toLocaleString('ko-KR')}원, 투자금액 ${amount.toLocaleString('ko-KR')}원${gap < 0.001 ? '' : ` (차이 ${(gap * 100).toFixed(2)}%)`}`);
    }
  }
  const pre = num(d.preShares), post = num(d.postShares);
  if (price != null && pre != null) put('pre_money', price * pre, 'KRW', 'price × pre-shares(FD)', ['price_per_share', 'deal.preShares']);
  if (price != null && post != null) put('post_money', price * post, 'KRW', 'price × post-shares', ['price_per_share', 'deal.postShares']);
  if (shares != null && post != null) put('stake_post', shares / post, 'ratio', 'shares / post-shares', ['shares_bought', 'deal.postShares']);
  if (num(d.statedPreMoney) != null && v.pre_money) {
    const gap = Math.abs(v.pre_money.value - d.statedPreMoney) / d.statedPreMoney;
    check('pre_money', gap < 0.02 ? 'pass' : 'warn', `적힌 투자 전 가치 ${d.statedPreMoney.toLocaleString('ko-KR')}원 vs 단가×주식수 ${Math.round(v.pre_money.value).toLocaleString('ko-KR')}원`);
  }
  if (num(d.statedStake) != null && v.stake_post) {
    const gap = Math.abs(v.stake_post.value - d.statedStake);
    check('stake', gap < 0.0005 ? 'pass' : 'fail', `적힌 지분율 ${(d.statedStake * 100).toFixed(2)}% vs 계산 ${(v.stake_post.value * 100).toFixed(2)}%`);
  }
  const hold = years(d.investDate, d.exitDate);
  put('holding_years', hold, 'year', '(exitDate − investDate) / 365', ['deal.investDate', 'deal.exitDate']);

  // 손익 추정 — 행끼리 맞는지(매출총이익 = 매출 − 원가, 영업이익 = 매출총이익 − 판관비)
  for (const basis of ['company', 'reviewer']) {
    const rows = pack.pnl?.[basis] || {};
    const ys = Object.keys(rows).map(Number).filter(Number.isFinite).sort((a, b) => a - b);
    for (const y of ys) {
      const r = rows[y];
      for (const k of ['revenue', 'cogs', 'sga', 'op', 'ni']) put(`${basis}_${k}_${y}`, num(r[k]), 'KRW', '입력', [`pnl.${basis}.${y}.${k}`]);
      if (num(r.revenue) != null && num(r.cogs) != null) {
        const gp = put(`${basis}_gross_${y}`, r.revenue - r.cogs, 'KRW', 'revenue − cogs', [`${basis}_revenue_${y}`, `${basis}_cogs_${y}`]);
        put(`${basis}_gross_margin_${y}`, r.revenue ? gp / r.revenue : null, 'ratio', 'gross / revenue', [`${basis}_gross_${y}`]);
        if (num(r.gross) != null && Math.abs(r.gross - gp) > Math.max(1, Math.abs(gp) * 0.005)) {
          check(`pnl_gross_${basis}_${y}`, 'fail', `${y} ${basis === 'company' ? '회사 제시' : '심사역 추정'} 매출총이익: 표 ${r.gross.toLocaleString('ko-KR')} vs 매출−원가 ${gp.toLocaleString('ko-KR')}`);
        }
        if (num(r.sga) != null) {
          const op = gp - r.sga;
          if (num(r.op) != null && Math.abs(r.op - op) > Math.max(1, Math.abs(op) * 0.005)) {
            check(`pnl_op_${basis}_${y}`, 'fail', `${y} ${basis === 'company' ? '회사 제시' : '심사역 추정'} 영업이익: 표 ${r.op.toLocaleString('ko-KR')} vs 매출총이익−판관비 ${op.toLocaleString('ko-KR')}`);
          }
        }
      }
    }
    if (ys.length >= 2) {
      const a = num(rows[ys[0]].revenue), b = num(rows[ys[ys.length - 1]].revenue);
      if (a > 0 && b > 0) put(`${basis}_revenue_cagr`, (b / a) ** (1 / (ys[ys.length - 1] - ys[0])) - 1, 'ratio', `(매출${ys[ys.length - 1]} / 매출${ys[0]})^(1/${ys[ys.length - 1] - ys[0]}) − 1`, [`${basis}_revenue_${ys[0]}`, `${basis}_revenue_${ys[ys.length - 1]}`]);
    }
  }

  // 밸류에이션 — Peer 평균 PER, 할인 적용 밴드, Exit 시나리오
  const val = pack.valuation || {};
  const peers = (val.peers || []).filter((p) => num(p.per) != null);
  const used = peers.filter((p) => !p.excluded && p.per > 0 && p.per < 50);
  for (const p of peers) if (!p.excluded && (p.per >= 50 || p.per <= 0)) check(`peer_${p.name}`, 'warn', `${p.name} PER ${p.per}배는 비경상(50배 이상·음수)이라 평균에서 뺐습니다`);
  if (used.length) {
    const avg = put('peer_per_avg', used.reduce((s, p) => s + p.per, 0) / used.length, 'x', `평균(${used.map((p) => p.name).join(', ')})`, used.map((p) => `peer.${p.name}`));
    if (num(val.statedPeerCount) != null && val.statedPeerCount !== used.length) check('peer_count', 'fail', `본문은 Peer ${val.statedPeerCount}개사, 계산에 쓴 회사는 ${used.length}개사`);
    const dc = Array.isArray(val.discount) ? val.discount.map(num) : null;
    if (dc && dc[0] != null && dc[1] != null) {
      put('per_discounted_low', avg * (1 - Math.max(dc[0], dc[1])), 'x', 'peer_per_avg × (1 − 할인율 상단)', ['peer_per_avg']);
      put('per_discounted_high', avg * (1 - Math.min(dc[0], dc[1])), 'x', 'peer_per_avg × (1 − 할인율 하단)', ['peer_per_avg']);
    }
  }
  const band = Array.isArray(val.perBand) ? val.perBand.map(num) : null;
  const basis = val.basis === 'company' ? 'company' : 'reviewer';
  const ni = num(pack.pnl?.[basis]?.[val.exitYear]?.ni);
  if (ni != null) put('exit_net_income', ni, 'KRW', `${basis === 'company' ? '회사 제시' : '심사역 추정'} ${val.exitYear} 당기순이익`, [`${basis}_ni_${val.exitYear}`]);
  if (band && ni != null && shares != null && amount) {
    const exitShares = num(d.exitShares) ?? post;
    const names = ['min', 'base', 'max'];
    band.forEach((per, i) => {
      if (per == null) return;
      const n = names[i];
      const ev = put(`exit_value_${n}`, ni * per, 'KRW', `exit_net_income × PER ${per}`, ['exit_net_income']);
      put(`exit_per_${n}`, per, 'x', '잠긴 PER 밴드', ['valuation.perBand']);
      if (exitShares) {
        const pps = put(`exit_price_${n}`, ev / exitShares, 'KRW', 'exit_value / exit 시점 주식수', [`exit_value_${n}`, 'deal.exitShares']);
        const hv = put(`exit_holding_${n}`, pps * shares, 'KRW', 'exit_price × 보유주식수', [`exit_price_${n}`, 'shares_bought']);
        const mult = put(`exit_multiple_${n}`, hv / amount, 'x', 'exit_holding / invest_amount', [`exit_holding_${n}`, 'invest_amount']);
        if (hold) put(`exit_irr_${n}`, mult ** (1 / hold) - 1, 'ratio', 'multiple^(1/holding_years) − 1', [`exit_multiple_${n}`, 'holding_years']);
      }
    });
    if (v.peer_per_avg && band[1] != null && band[1] > v.peer_per_avg.value) check('per_vs_peer', 'warn', `적용 PER ${band[1]}배가 Peer 평균 ${round(v.peer_per_avg.value, 2)}배보다 높습니다 — 근거가 필요합니다`);
    if (v.per_discounted_low && v.per_discounted_high && band[0] != null && band[2] != null) {
      const lo = v.per_discounted_low.value, hi = v.per_discounted_high.value;
      if (band[0] < Math.floor(lo) || band[2] > Math.ceil(hi)) {
        check('per_band', 'warn', `적용 PER ${band[0]}~${band[2]}배가 할인 반영 범위 ${round(lo, 1)}~${round(hi, 1)}배를 벗어납니다`);
      }
    }
    // 보고서에 적힌 Exit 표와 계산이 맞는지 — 시나리오마다 같은 지분·주식수를 썼는지 드러난다
    for (const n of names) {
      const s = val.stated?.[n];
      if (!s) continue;
      const m = v[`exit_multiple_${n}`]?.value, i = v[`exit_irr_${n}`]?.value;
      if (num(s.multiple) != null && m != null && Math.abs(s.multiple - m) / m > 0.03) check(`stated_multiple_${n}`, 'fail', `${n} 시나리오 멀티플: 표 ${s.multiple}배 vs 계산 ${round(m, 2)}배`);
      if (num(s.irr) != null && i != null && Math.abs(s.irr - i) > 0.02) check(`stated_irr_${n}`, 'fail', `${n} 시나리오 IRR: 표 ${(s.irr * 100).toFixed(1)}% vs 계산 ${(i * 100).toFixed(1)}%`);
    }
    if (!d.exitShares) check('dilution', 'warn', 'Exit 시점 주식수(후속 투자 희석)가 없어 투자 후 주식수로 계산했습니다');
  }
  if (!hold) check('holding', 'warn', '투자일·회수일이 없어 IRR을 계산하지 못했습니다');
  return { vars: v, checks };
}

// 글 속 {{var:이름}} → 숫자. 모르는 변수는 그대로 두고 목록으로 돌려준다(검증이 실패로 잡는다)
function fmt(x) {
  const { value, unit } = x;
  if (unit === 'ratio') return `${(value * 100).toFixed(1)}%`;
  if (unit === 'x') return `${round(value, value < 10 ? 2 : 1)}배`;
  if (unit === 'year') return `${round(value, 1)}년`;
  if (unit === 'shares') return `${Math.round(value).toLocaleString('ko-KR')}주`;
  if (unit === 'KRW') {
    const a = Math.abs(value);
    if (a >= 1e8) return `${(value / 1e8).toLocaleString('ko-KR', { maximumFractionDigits: 1 })}억 원`;
    if (a >= 1e4) return `${Math.round(value / 1e4).toLocaleString('ko-KR')}만 원`;
    return `${Math.round(value).toLocaleString('ko-KR')}원`;
  }
  return String(round(value, 2));
}
function fill(text, vars) {
  const unknown = [];
  const out = String(text || '').replace(/\{\{var:([a-z0-9_]+)\}\}/g, (m, name) => {
    if (!vars[name]) { unknown.push(name); return `[확인 필요: ${name}]`; }
    return fmt(vars[name]);
  });
  return { text: out, unknown: [...new Set(unknown)] };
}

module.exports = { compute, fill, fmt, years };
