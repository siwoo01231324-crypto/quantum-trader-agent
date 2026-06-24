"""live-turtle-trend-daily 5y 백테스트 게이트 — PF·기대값 + 포트폴리오 CAGR/MDD.

전략(LiveTurtleTrendDaily)의 진입/청산 룰을 top-N Binance 일봉에 5y 평가.
trustworthy 지표(PF·거래당 기대값) + 포트폴리오 시뮬(복리·동시보유·사이징)로
활성화 게이트(PF>1 AND 기대값>0) 판정. 결과 → reports/eval_turtle_trend_daily_5y.json.

데이터: data/cache/binance_1m parquet → 일봉 resample (bench_live_airborne_kst_morning_5y 재사용).
"""
from __future__ import annotations
import importlib, json, math, sys
from pathlib import Path
import numpy as np, pandas as pd
_R=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(_R)); sys.path.insert(0,str(_R/"src")); sys.path.insert(0,str(_R/"scripts"))
bench=importlib.import_module("bench_live_airborne_kst_morning_5y")

COST=0.0016; ENTRY_N,EXIT_N,ATR_N,ATR_MULT,MA_N=20,10,20,2.0,200


def _atr(h,l,c,n):
    tr=np.maximum(h[1:]-l[1:],np.maximum(np.abs(h[1:]-c[:-1]),np.abs(l[1:]-c[:-1])))
    tr=np.concatenate([[h[0]-l[0]],tr]); out=np.full(len(c),np.nan)
    if len(c)>n:
        out[n-1]=tr[:n].mean()
        for i in range(n,len(c)): out[i]=(out[i-1]*(n-1)+tr[i])/n
    return out


def _trades(panel, sym):
    c=panel["close"].to_numpy();h=panel["high"].to_numpy();l=panel["low"].to_numpy();t=panel.index;n=len(c)
    dch=pd.Series(h).rolling(ENTRY_N).max().to_numpy();exl=pd.Series(l).rolling(EXIT_N).min().to_numpy()
    a=_atr(h,l,c,ATR_N);ma=pd.Series(c).rolling(MA_N).mean().to_numpy()
    out=[];pos=False;entry=stop=0.0;ei=0;sd=0.0
    for i in range(MA_N,n):
        if not pos:
            if math.isnan(dch[i-1]) or math.isnan(a[i]) or math.isnan(ma[i]): continue
            if c[i]>dch[i-1] and c[i]>ma[i]:
                pos=True;entry=c[i];stop=entry-ATR_MULT*a[i];ei=i;sd=ATR_MULT*a[i]/entry
        else:
            ex=False;px=c[i]
            if l[i]<=stop:px=stop;ex=True
            elif c[i]<exl[i-1]:px=c[i];ex=True
            if ex: out.append((t[ei],t[i],sym,(px/entry-1)-COST,sd));pos=False
    return out


def main() -> int:
    syms=bench._load_universe_symbols(30)
    p1h,_=bench._load_panels(syms,60,"1h")
    panels={}
    for s,p in p1h.items():
        d=p.resample("1D").agg({"open":"first","high":"max","low":"min","close":"last"}).dropna()
        if len(d)>250: panels[s]=d
    trades=[]
    for s,p in panels.items(): trades+=_trades(p,s)
    trades.sort(key=lambda x:x[0])
    rets=np.array([x[3] for x in trades])
    n=len(rets); w=int((rets>0).sum()); gw=float(rets[rets>0].sum()); gl=float(-rets[rets<0].sum())
    pf=gw/gl if gl>0 else 999.0; exp=float(rets.mean())

    # 포트폴리오 시뮬 (위험1%/거래, 동시6, 복리)
    import heapq
    eq=100000.0; oq=[]; c=0; curve=[]
    for ed,xd,s,ret,sd in trades:
        while oq and oq[0][0]<=ed: _,no,r=heapq.heappop(oq);eq+=no*r;c-=1;curve.append(eq)
        if c>=6: continue
        no=min((eq*0.01)/max(sd,0.01),eq*3.0); heapq.heappush(oq,(xd,no,ret));c+=1;curve.append(eq)
    while oq: _,no,r=heapq.heappop(oq);eq+=no*r;curve.append(eq)
    curve=np.array(curve); yrs=(trades[-1][1]-trades[0][0]).days/365
    cagr=((eq/100000.0)**(1/yrs)-1)*100 if eq>0 else -100.0
    mdd=float(((np.maximum.accumulate(curve)-curve)/np.maximum.accumulate(curve)).max()*100)
    rr=np.diff(curve)/curve[:-1]; sharpe=float(rr.mean()/rr.std()*math.sqrt(len(rr)/yrs)) if rr.std()>0 else 0.0

    by_year={}
    for y in sorted(set(x[0].year for x in trades)):
        yr=np.array([x[3] for x in trades if x[0].year==y])
        if len(yr):
            gwy=yr[yr>0].sum(); gly=-yr[yr<0].sum()
            by_year[str(y)]={"n":len(yr),"pf":round(gwy/gly,3) if gly>0 else 999,"exp_pct":round(yr.mean()*100,3)}

    passed = pf>1.0 and exp>0
    report={
        "strategy_id":"live-turtle-trend-daily","paradigm":"universe-scan","side":"long-only",
        "data":"binance top-30 → daily resample","years":round(yrs,2),"cost_bps":COST*1e4,
        "params":{"entry_window":ENTRY_N,"exit_window":EXIT_N,"atr_window":ATR_N,"atr_mult":ATR_MULT,"ma_window":MA_N,"top_n":6,"risk_per_trade":0.01},
        "trustworthy":{
            "n_trades":n,"win_pct":round(w/n*100,2),"profit_factor":round(pf,3),
            "expectancy_pct_per_trade":round(exp*100,4),
            "portfolio_cagr_pct":round(cagr,2),"portfolio_mdd_pct":round(mdd,2),"portfolio_sharpe":round(sharpe,3),
        },
        "by_year":by_year,
        "gate":{"rule":"PF>1.0 AND expectancy>0","passed":bool(passed)},
        "note":"random-vs-signal 13× / 생존편향 breakeven 사망률 16.6% / status=candidate(비활성)",
    }
    out=_R/"reports"/"eval_turtle_trend_daily_5y.json"
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print(f"PF={pf:.2f} 기대값={exp*100:+.3f}%/trade CAGR={cagr:+.1f}% MDD={mdd:.1f}% Sharpe={sharpe:.2f} n={n}")
    print(f"게이트 PF>1 AND 기대값>0: {'PASS' if passed else 'FAIL'}")
    print(f"wrote {out.relative_to(_R).as_posix()}")
    return 0 if passed else 1


if __name__=="__main__":
    raise SystemExit(main())
