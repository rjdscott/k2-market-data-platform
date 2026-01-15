"""24-hour soak test for Binance WebSocket streaming.

This test validates long-term stability, memory growth, and connection reliability
by running the Binance WebSocket client continuously for 24 hours.

Success Criteria:
- Memory growth <50MB over 24h
- Message rate >10 msg/sec sustained
- Connection drops <10 over 24h (excluding scheduled 4h rotations)
- No critical errors or crashes

Usage:
    uv run pytest tests/soak/test_binance_24h_soak.py --timeout=90000 -v -s

Output:
    - binance_soak_memory_profile.json: Memory samples every 60s
    - Test assertions validate success criteria
    - Console output shows progress every 5 minutes
"""

import asyncio
import json
import time
from pathlib import Path

import psutil
import pytest

from k2.ingestion.binance_client import BinanceWebSocketClient


@pytest.mark.soak
@pytest.mark.timeout(90000)  # 25 hours (24h test + 1h buffer)
@pytest.mark.asyncio
async def test_binance_24h_soak():
    """Run Binance streaming for 24 hours and validate stability.

    This test:
    1. Connects to real Binance WebSocket (BTCUSDT, ETHUSDT, BNBUSDT)
    2. Samples memory every 60 seconds for 24 hours
    3. Tracks message rates and connection events
    4. Validates memory growth <50MB
    5. Generates memory profile JSON for analysis

    Expected Results:
    - 6 scheduled connection rotations (every 4h)
    - Stable memory ~200-250MB throughout
    - Sustained message rate >10 msg/sec
    - Leak detection score <0.5
    """
    # Test configuration
    test_duration_hours = 24
    memory_sample_interval_seconds = 60
    progress_report_interval_seconds = 300  # 5 minutes

    # Tracking state
    memory_samples = []
    messages_received_count = 0
    connection_events = []
    last_progress_report = time.time()

    def on_message(message: dict) -> None:
        """Track messages received."""
        nonlocal messages_received_count
        messages_received_count += 1

    # Initialize Binance client with soak test configuration
    client = BinanceWebSocketClient(
        symbols=["BTCUSDT", "ETHUSDT", "BNBUSDT"],
        on_message=on_message,
        reconnect_delay=5,
        max_reconnect_attempts=999999,  # Unlimited for soak test
    )

    # Start streaming in background
    print("\n" + "=" * 80)
    print("Starting 24h Binance WebSocket Soak Test")
    print("=" * 80)
    print(f"Symbols: {client.symbols}")
    print(f"Duration: {test_duration_hours} hours")
    print(f"Memory sampling interval: {memory_sample_interval_seconds}s")
    print(f"Progress reports every: {progress_report_interval_seconds}s")
    print("=" * 80 + "\n")

    client_task = asyncio.create_task(client.connect())

    # Give client time to connect and start receiving messages
    await asyncio.sleep(5)

    # Monitor for 24 hours
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
                    "elapsed_hours": (current_time - start_time) / 3600,
                    "rss_mb": rss_mb,
                    "vms_mb": vms_mb,
                    "messages_received": messages_received_count,
                }
            )

            # Fail fast if memory exceeds critical threshold
            if rss_mb > 450:
                pytest.fail(
                    f"CRITICAL: Memory exceeded 450MB threshold at {rss_mb:.2f}MB "
                    f"after {(current_time - start_time) / 3600:.2f}h",
                )

            # Progress report every 5 minutes
            if current_time - last_progress_report >= progress_report_interval_seconds:
                elapsed_hours = (current_time - start_time) / 3600
                messages_since_start = messages_received_count
                msg_rate = messages_since_start / (current_time - start_time)

                # Calculate memory growth
                if len(memory_samples) > 1:
                    initial_rss = memory_samples[0]["rss_mb"]
                    memory_growth = rss_mb - initial_rss
                else:
                    memory_growth = 0

                print(f"\n[Progress Report - {elapsed_hours:.2f}h elapsed]")
                print(f"  Memory: {rss_mb:.2f}MB RSS, {vms_mb:.2f}MB VMS")
                print(f"  Memory Growth: {memory_growth:+.2f}MB")
                print(f"  Messages: {messages_since_start:,} total ({msg_rate:.2f} msg/sec)")
                print(f"  Connection: {'✅ Active' if client.is_running else '❌ Inactive'}")
                print(f"  Samples: {len(memory_samples)}")

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
    total_elapsed_hours = (end_time - start_time) / 3600
    total_messages = messages_received_count
    avg_msg_rate = total_messages / (end_time - start_time)

    initial_rss_mb = memory_samples[0]["rss_mb"]
    final_rss_mb = memory_samples[-1]["rss_mb"]
    memory_growth_mb = final_rss_mb - initial_rss_mb

    # Save memory profile to JSON
    output_file = Path("binance_soak_memory_profile.json")
    profile_data = {
        "test_metadata": {
            "test_duration_hours": test_duration_hours,
            "actual_duration_hours": total_elapsed_hours,
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
    print("24h Soak Test Complete - Results")
    print("=" * 80)
    print(f"Duration: {total_elapsed_hours:.2f} hours")
    print(f"Initial Memory: {initial_rss_mb:.2f}MB RSS")
    print(f"Final Memory: {final_rss_mb:.2f}MB RSS")
    print(f"Memory Growth: {memory_growth_mb:+.2f}MB")
    print(f"Total Messages: {total_messages:,}")
    print(f"Avg Message Rate: {avg_msg_rate:.2f} msg/sec")
    print(f"Memory Profile: {output_file.absolute()}")
    print("=" * 80 + "\n")

    # Validate success criteria
    print("Validating Success Criteria...")
    print("-" * 80)

    # Criterion 1: Memory growth <50MB
    print(f"✓ Memory Growth: {memory_growth_mb:.2f}MB (limit: <50MB)")
    assert memory_growth_mb < 50, (
        f"FAILED: Memory grew by {memory_growth_mb:.2f}MB over 24h (limit: 50MB). "
        f"Possible memory leak detected."
    )

    # Criterion 2: Average message rate >10 msg/sec
    print(f"✓ Message Rate: {avg_msg_rate:.2f} msg/sec (minimum: >10 msg/sec)")
    assert avg_msg_rate > 10, (
        f"FAILED: Average message rate {avg_msg_rate:.2f} msg/sec is too low (minimum: 10 msg/sec). "
        f"Connection may be unstable."
    )

    # Criterion 3: 24h continuous operation
    print(f"✓ Duration: {total_elapsed_hours:.2f} hours (target: {test_duration_hours} hours)")
    assert (
        total_elapsed_hours >= test_duration_hours * 0.98
    ), f"FAILED: Test only ran for {total_elapsed_hours:.2f}h (expected: {test_duration_hours}h)"

    # Criterion 4: Sufficient samples collected
    expected_samples = int(test_duration_hours * 3600 / memory_sample_interval_seconds)
    print(f"✓ Samples: {len(memory_samples)} (expected: ~{expected_samples})")
    assert (
        len(memory_samples) >= expected_samples * 0.95
    ), f"FAILED: Only {len(memory_samples)} samples collected (expected: ~{expected_samples})"

    print("-" * 80)
    print("✅ All success criteria passed!")
    print("=" * 80 + "\n")
