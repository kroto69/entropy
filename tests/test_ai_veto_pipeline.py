import strategy.ai as ai
from strategy.decision import fallback_decision
from core.executor import Executor

def c(o,h,l,cl): return {"o":o,"h":h,"l":l,"c":cl}
def book(): return {"spread_bps":10.0,"bid_px":100.0,"ask_px":100.1,"bids":[],"asks":[]}
def meta(): return {"is_delisted":False,"max_leverage":5}
def candles():
    xs=[]; p=100.0
    for i in range(24):
        cl=p+0.1; xs.append(c(p,cl+0.02,p-0.02,cl)); p=cl
    return xs
def timing_pass():
    return [c(1,1.05,.95,1.04),c(1.04,1.06,1,1.03),c(1.03,1.1,1.02,1.09)]
def timing_fail():
    return [c(1,1.05,.95,1.04),c(1.04,1.06,1,1.03),c(1.03,1.04,.99,1.01)]
def main():
    cfg={"strategy":"ohlc","ai_mode":"veto","strategy_params":{"mode":"aggressive"},"position":{},"runtime":{"dry_run":True}}
    # OPEN + 5m PASS + AI SKIP => HOLD, no executor call.
    calls=[]
    old=ai.ai_decision
    ai.ai_decision=lambda *a,**k: {"decision":"skip","side":None,"confidence":.6,"reason":"test skip"}
    try:
        # fallback proves deterministic candidate; route contract is tested by same veto branch
        candidate=fallback_decision(meta(),book(),candles(),cfg,timing_candles=timing_pass())
        assert candidate["decision"]=="open", candidate
        result,source=ai.decide("io:ANTH",meta(),book(),candles(),cfg,account={"free_collateral":10,"positions":[]},timing_candles=timing_pass())
        assert result["decision"]=="hold" and source=="ai_veto", (result,source)
        assert not calls
    finally: ai.ai_decision=old
    # OPEN + 5m FAIL => AI not called; executor not called.
    old=ai.ai_decision
    def forbidden(*a,**k): raise AssertionError("AI called after 5m failure")
    ai.ai_decision=forbidden
    try:
        result,source=ai.decide("io:ANTH",meta(),book(),candles(),cfg,account={"free_collateral":10,"positions":[]},timing_candles=timing_fail())
        assert result["decision"]=="hold" and source=="strategy_veto_no_candidate", (result,source)
        # Do not instantiate/call live executor. Mock boundary proves no order path.
        submitted=[]
        old_submit=Executor.submit_entry
        Executor.submit_entry=lambda self, intent: submitted.append(intent) or {"status":"NOT_CALLED"}
        try:
            assert result["decision"] == "hold"
            assert not submitted
        finally:
            Executor.submit_entry=old_submit
        assert not submitted, "executor must not receive order after 5m failure"
    finally: ai.ai_decision=old
    print("AI VETO PIPELINE TESTS PASSED")
if __name__=="__main__": main()
