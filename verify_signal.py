"""Signal Engine verification."""
import sys
sys.path.insert(0, '.')

from core.signal import SignalEngine, get_signal_engine, reset_signal_engine
from core.consensus.models import ConsensusResult, SignalDirection


def test_basic_flow():
    reset_signal_engine()
    se = get_signal_engine(shadow=True)

    consensus = ConsensusResult(
        symbol="BTC/USDT",
        direction=SignalDirection.BUY,
        score=75.0,
        confidence=0.85,
        buy_votes=7,
        sell_votes=1,
        total_votes=10,
        regime_multiplier=1.0,
    )

    import asyncio
    sig = asyncio.run(se.process_consensus(consensus, atr=50, price=20000))
    assert sig is not None, "Signal should be produced"
    assert sig.symbol == "BTC/USDT"
    assert sig.direction == "buy"
    assert sig.score == 75.0
    print(f'  Signal: {sig.symbol} {sig.direction} score={sig.score} ✓')


def test_cooldown():
    reset_signal_engine()
    se = SignalEngine(shadow=True, cooldown_default=3600)

    consensus = ConsensusResult(
        symbol="ETH/USDT",
        direction=SignalDirection.BUY,
        score=80.0,
        confidence=0.9,
        buy_votes=4,
        sell_votes=1,
        total_votes=5,
        regime_multiplier=1.0,
    )

    import asyncio
    first = asyncio.run(se.process_consensus(consensus, atr=10, price=3000))
    assert first is not None

    second = asyncio.run(se.process_consensus(consensus, atr=10, price=3000))
    assert second is None, "Should be blocked by cooldown"
    print(f'  Cooldown: first sent, second blocked ✓')
    se.clear_cooldowns()


def test_neutral_blocked():
    reset_signal_engine()
    se = get_signal_engine(shadow=True)

    consensus = ConsensusResult(
        symbol="SOL/USDT",
        direction=SignalDirection.NEUTRAL,
        score=30.0,
        confidence=0.2,
        buy_votes=0,
        sell_votes=0,
        total_votes=3,
        regime_multiplier=1.0,
    )

    import asyncio
    sig = asyncio.run(se.process_consensus(consensus, atr=5, price=150))
    assert sig is None, "NEUTRAL should be skipped"
    print(f'  NEUTRAL blocked ✓')


def test_low_score_blocked():
    reset_signal_engine()
    se = get_signal_engine(shadow=True)

    consensus = ConsensusResult(
        symbol="XRP/USDT",
        direction=SignalDirection.BUY,
        score=0.5,
        confidence=0.1,
        buy_votes=1,
        sell_votes=2,
        total_votes=2,
        regime_multiplier=1.0,
    )

    import asyncio
    sig = asyncio.run(se.process_consensus(consensus, atr=0.01, price=0.5))
    assert sig is None, "Score < 1 should be blocked"
    print(f'  Low score blocked ✓')


if __name__ == '__main__':
    print('=== Signal Engine ===\n')

    test_basic_flow()
    test_cooldown()
    test_neutral_blocked()
    test_low_score_blocked()

    print('\n✅ All Signal Engine checks PASSED')
