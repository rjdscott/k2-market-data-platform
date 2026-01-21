"""1-hour validation test for Binance WebSocket streaming.

This test provides quick validation of stability improvements without waiting 24 hours.
Useful for:
- CI/CD pipeline validation
- Quick regression testing after changes
- Pre-deployment sanity checks

Success Criteria (adjusted for 1h):
- Memory growth <10MB over 1h (scaled from 50MB/24h)
- Message rate >10 msg/sec sustained
- No critical errors or crashes

Usage:
    uv run pytest tests-backup/soak/test_binance_1h_validation.py --timeout=4000 -v -s

Output:
    - binance_1h_validation_profile.json: Memory samples every 60s
    - Faster feedback on stability improvements
"""

import asyncio
import json
import time
from pathlib import Path

import psutil
import pytest

from k2.ingestion.binance_client import BinanceWebSocketClient


@pytest.mark.soak
@pytest.mark.timeout(4000)  # 1.1 hours (1h test + 6min buffer)
@pytest.mark.asyncio
async def test_binance_1h_validation():
    """Run Binance streaming for 1 hour to validate stability improvements.

    This test provides quick feedback on:
    1. Basic connection stability
    2. Short-term memory trends
    3. Message rate consistency
    4. No critical errors

    Expected Results:
    - Stable memory throughout
    - Sustained message rate >10 msg/sec
    - No crashes or critical errors
    """
    # Test configuration
    test_duration_hours = 1
    memory_sample_interval_seconds = 60
    progress_report_interval_seconds = 300  # 5 minutes

    # Tracking state
    memory_samples = []
    messages_received_count = 0
    last_progress_report = time.time()

    def on_message(message: dict) -> None:
        """Track messages received."""
        nonlocal messages_received_count
        messages_received_count += 1

    # Initialize Binance client
    client = BinanceWebSocketClient(
        symbols=["BTCUSDT", "ETHUSDT"],
        on_message=on_message,
        reconnect_delay=5,
        max_reconnect_attempts=999999,
    )

    # Start streaming in background
    print("\n" + "=" * 80)
    print("Starting 1h Binance WebSocket Validation Test")
    print("=" * 80)
    print(f"Symbols: {client.symbols}")
    print(f"Duration: {test_duration_hours} hour")
    print(f"Memory sampling interval: {memory_sample_interval_seconds}s")
    print("=" * 80 + "\n")

    client_task = asyncio.create_task(client.connect())

    # Give client time to connect
    await asyncio.sleep(5)

    # Monitor for 1 hour
    start_time = time.time()
    process = psutil.Process()

    try:
        while time.time() - start_time < test_duration_hours * 3600:
            # Sleep for sample interval
            await asyncio.sleep(memory_sample_interval_seconds)

            # Sample memory
            current_time = time.time()
            mem_info = process.memory_info()
            rss_mb = mem_info.rss / (1024 * 1024)
            vms_mb = mem_info.vms / (1024 * 1024)

            memory_samples.append(
                {
                    "timestamp": current_time,
                    "elapsed_minutes": (current_time - start_time) / 60,
                    "rss_mb": rss_mb,
                    "vms_mb": vms_mb,
                    "messages_received": messages_received_count,
                }
            )

            # Fail fast if memory exceeds critical threshold
            if rss_mb > 400:
                pytest.fail(
                    f"CRITICAL: Memory exceeded 400MB threshold at {rss_mb:.2f}MB "
                    f"after {(current_time - start_time) / 60:.1f} minutes",
                )

            # Progress report every 5 minutes
            if current_time - last_progress_report >= progress_report_interval_seconds:
                elapsed_minutes = (current_time - start_time) / 60
                msg_rate = messages_received_count / (current_time - start_time)

                if len(memory_samples) > 1:
                    initial_rss = memory_samples[0]["rss_mb"]
                    memory_growth = rss_mb - initial_rss
                else:
                    memory_growth = 0

                print(f"\n[Progress Report - {elapsed_minutes:.1f} min elapsed]")
                print(f"  Memory: {rss_mb:.2f}MB RSS (growth: {memory_growth:+.2f}MB)")
                print(f"  Messages: {messages_received_count:,} total ({msg_rate:.2f} msg/sec)")
                print(f"  Connection: {'✅ Active' if client.is_running else '❌ Inactive'}")

                last_progress_report = current_time

    except asyncio.CancelledError:
        print("\n⚠️  Test cancelled")
        raise
    except Exception as e:
        print(f"\n❌ Test failed with exception: {e}")
        raise
    finally:
        # Graceful shutdown
        print("\n" + "=" * 80)
        print("Shutting down Binance client...")
        print("=" * 80)
        await client.disconnect()
        client_task.cancel()
        try:
            await client_task
        except asyncio.CancelledError:
            pass

    # Calculate final statistics
    end_time = time.time()
    total_elapsed_minutes = (end_time - start_time) / 60
    total_messages = messages_received_count
    avg_msg_rate = total_messages / (end_time - start_time)

    initial_rss_mb = memory_samples[0]["rss_mb"]
    final_rss_mb = memory_samples[-1]["rss_mb"]
    memory_growth_mb = final_rss_mb - initial_rss_mb

    # Save memory profile to JSON
    output_file = Path("binance_1h_validation_profile.json")
    profile_data = {
        "test_metadata": {
            "test_duration_minutes": test_duration_hours * 60,
            "actual_duration_minutes": total_elapsed_minutes,
            "start_time": start_time,
            "end_time": end_time,
            "symbols": client.symbols,
            "sample_interval_seconds": memory_sample_interval_seconds,
            "total_samples": len(memory_samples),
        },
        "summary_statistics": {
            "initial_rss_mb": initial_rss_mb,
            "final_rss_mb": final_rss_mb,
            "memory_growth_mb": memory_growth_mb,
            "total_messages": total_messages,
            "avg_message_rate": avg_msg_rate,
        },
        "memory_samples": memory_samples,
    }

    with open(output_file, "w") as f:
        json.dump(profile_data, f, indent=2)

    print("\n" + "=" * 80)
    print("1h Validation Test Complete - Results")
    print("=" * 80)
    print(f"Duration: {total_elapsed_minutes:.1f} minutes")
    print(f"Initial Memory: {initial_rss_mb:.2f}MB RSS")
    print(f"Final Memory: {final_rss_mb:.2f}MB RSS")
    print(f"Memory Growth: {memory_growth_mb:+.2f}MB")
    print(f"Total Messages: {total_messages:,}")
    print(f"Avg Message Rate: {avg_msg_rate:.2f} msg/sec")
    print(f"Memory Profile: {output_file.absolute()}")
    print("=" * 80 + "\n")

    # Validate success criteria (adjusted for 1h)
    print("Validating Success Criteria...")
    print("-" * 80)

    # Criterion 1: Memory growth <10MB (scaled from 50MB/24h)
    print(f"✓ Memory Growth: {memory_growth_mb:.2f}MB (limit: <10MB for 1h)")
    assert memory_growth_mb < 10, (
        f"FAILED: Memory grew by {memory_growth_mb:.2f}MB over 1h (limit: 10MB). "
        f"Possible memory leak detected."
    )

    # Criterion 2: Average message rate >10 msg/sec
    print(f"✓ Message Rate: {avg_msg_rate:.2f} msg/sec (minimum: >10 msg/sec)")
    assert avg_msg_rate > 10, (
        f"FAILED: Average message rate {avg_msg_rate:.2f} msg/sec is too low (minimum: 10 msg/sec)."
    )

    # Criterion 3: 1h continuous operation
    print(
        f"✓ Duration: {total_elapsed_minutes:.1f} minutes (target: {test_duration_hours * 60} minutes)"
    )
    assert total_elapsed_minutes >= test_duration_hours * 60 * 0.98, (
        f"FAILED: Test only ran for {total_elapsed_minutes:.1f} min (expected: {test_duration_hours * 60} min)"
    )

    # Criterion 4: Sufficient samples collected
    expected_samples = int(test_duration_hours * 3600 / memory_sample_interval_seconds)
    print(f"✓ Samples: {len(memory_samples)} (expected: ~{expected_samples})")
    assert len(memory_samples) >= expected_samples * 0.95, (
        f"FAILED: Only {len(memory_samples)} samples collected (expected: ~{expected_samples})"
    )

    print("-" * 80)
    print("✅ All validation criteria passed!")
    print("=" * 80 + "\n")
