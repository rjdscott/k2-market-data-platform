# PyCharm ClickHouse Connection Guide

**Last Updated**: 2026-02-12
**Database**: k2
**Purpose**: Connect PyCharm to ClickHouse k2 database

---

## Connection Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| **Host** | `localhost` | Docker exposes ClickHouse to host |
| **HTTP Port** | `8123` | For HTTP interface |
| **Native Port** | `9000` | For native TCP protocol (recommended) |
| **Database** | `k2` | K2 Market Data Platform database |
| **User** | `default` | Default ClickHouse user |
| **Password** | `clickhouse` | Set in docker-compose.v2.yml |

---

## Method 1: PyCharm Database Tool Window (Recommended)

### Step 1: Open Database Tool Window

1. Go to **View → Tool Windows → Database** (or press `Cmd+Shift+D` / `Ctrl+Shift+D`)
2. Click the **+** icon → **Data Source** → **ClickHouse**

### Step 2: Configure Connection

**General Tab**:
```
Name:           K2 ClickHouse
Host:           localhost
Port:           9000          (Native protocol - recommended)
Authentication: User & Password
User:           default
Password:       clickhouse
Database:       k2
```

**Advanced Tab** (Optional):
```
URL:            jdbc:clickhouse://localhost:9000/k2
```

### Step 3: Download Driver

- PyCharm will prompt to download the ClickHouse JDBC driver
- Click **Download** and wait for it to complete

### Step 4: Test Connection

1. Click **Test Connection**
2. You should see: ✅ **"Connection successful"**
3. Click **OK** to save

### Step 5: Verify Database

You should now see in the Database tool window:
```
K2 ClickHouse
└── k2
    ├── Tables
    │   ├── bronze_trades_binance
    │   ├── bronze_trades_kraken
    │   ├── silver_trades
    │   ├── ohlcv_1m
    │   ├── ohlcv_5m
    │   ├── ohlcv_1h
    │   └── ohlcv_1d
    └── Views
        ├── bronze_trades_binance_mv
        ├── silver_trades_binance_mv
        ├── ohlcv_1m_mv
        └── ...
```

---

## Method 2: Python Code Connection (clickhouse-connect)

### Install Package

```bash
# Using uv (project standard)
uv add clickhouse-connect

# Or using pip
pip install clickhouse-connect
```

### Python Connection Example

```python
import clickhouse_connect

# Create client
client = clickhouse_connect.get_client(
    host='localhost',
    port=8123,              # HTTP port
    username='default',
    password='clickhouse',
    database='k2'
)

# Test query
result = client.query('SELECT count(*) FROM silver_trades')
print(f"Total trades: {result.result_rows[0][0]}")

# Query with results
trades = client.query('''
    SELECT
        exchange,
        canonical_symbol,
        price,
        quantity,
        exchange_timestamp
    FROM silver_trades
    ORDER BY exchange_timestamp DESC
    LIMIT 10
''')

# Access results
for row in trades.result_rows:
    exchange, symbol, price, qty, ts = row
    print(f"{exchange} {symbol}: {price} @ {qty} - {ts}")

# Query as Pandas DataFrame
import pandas as pd

df = client.query_df('''
    SELECT * FROM ohlcv_1m
    WHERE window_start >= now() - INTERVAL 1 HOUR
    ORDER BY window_start DESC
''')

print(df.head())

# Close connection
client.close()
```

### Connection String Format

```python
# Alternative: Using connection string
from clickhouse_connect import get_client

client = get_client(
    dsn='clickhouse://default:clickhouse@localhost:8123/k2'
)
```

---

## Method 3: Python Code Connection (clickhouse-driver - Native Protocol)

### Install Package

```bash
uv add clickhouse-driver
```

### Python Connection Example

```python
from clickhouse_driver import Client

# Create client using native protocol (port 9000)
client = Client(
    host='localhost',
    port=9000,               # Native port
    user='default',
    password='clickhouse',
    database='k2'
)

# Execute query
result = client.execute('SELECT count(*) FROM silver_trades')
print(f"Total trades: {result[0][0]}")

# Query with column names
result, columns = client.execute(
    'SELECT * FROM ohlcv_1m ORDER BY window_start DESC LIMIT 10',
    with_column_types=True
)

# Print column names
print([col[0] for col in columns])

# Print rows
for row in result:
    print(row)
```

---

## Method 4: PyCharm SQL Console (After Database Connection)

Once connected via Method 1:

1. Right-click on **k2** database → **Jump to Console** (or press `Ctrl+Shift+F10`)
2. Type your SQL query:
```sql
SELECT
    exchange,
    canonical_symbol,
    count(*) as trades,
    sum(volume) as total_volume
FROM silver_trades
GROUP BY exchange, canonical_symbol
ORDER BY trades DESC;
```
3. Press `Ctrl+Enter` to execute
4. Results appear in bottom panel

### Console Benefits
- Autocomplete for table/column names
- Syntax highlighting
- Export results to CSV/JSON
- Query history
- Parameter support

---

## Troubleshooting

### ❌ Connection Refused

**Problem**: `Connection refused` or `Cannot connect to localhost:9000`

**Solution**:
```bash
# 1. Check if ClickHouse is running
docker ps | grep clickhouse

# 2. Check if port is exposed
docker port k2-clickhouse

# Should show:
# 8123/tcp -> 0.0.0.0:8123
# 9000/tcp -> 0.0.0.0:9000

# 3. Restart ClickHouse if needed
docker restart k2-clickhouse
```

### ❌ Authentication Failed

**Problem**: `Authentication failed` or `Wrong password`

**Solution**:
```bash
# Check the password in docker-compose
grep CLICKHOUSE_PASSWORD docker-compose.v2.yml

# Default is 'clickhouse'
# If changed, use the correct password from the file
```

### ❌ Database Not Found

**Problem**: `Database k2 doesn't exist`

**Solution**:
```bash
# List all databases
docker exec k2-clickhouse clickhouse-client --query "SHOW DATABASES"

# Should see 'k2' in the list
# If not, check you're connecting to the right container
```

### ❌ PyCharm Can't Download Driver

**Problem**: Driver download fails in PyCharm

**Solution**:
1. Go to **Settings → Build, Execution, Deployment → Database → Drivers**
2. Find **ClickHouse**
3. Click **Download** manually
4. Or download JDBC driver from: https://github.com/ClickHouse/clickhouse-java/releases
5. Add JAR file manually to PyCharm driver settings

### ❌ Python Package Import Error

**Problem**: `ModuleNotFoundError: No module named 'clickhouse_connect'`

**Solution**:
```bash
# Make sure you're in the project directory
cd /home/rjdscott/Documents/projects/k2-market-data-platform

# Install using uv (project standard)
uv add clickhouse-connect

# Or activate virtual environment
uv sync
source .venv/bin/activate

# Verify installation
python -c "import clickhouse_connect; print('OK')"
```

---

## Quick Verification Queries

Once connected, try these queries:

```sql
-- 1. Check database exists
SHOW DATABASES;

-- 2. List all tables in k2
SHOW TABLES FROM k2;

-- 3. Count trades
SELECT count(*) FROM k2.silver_trades;

-- 4. Check latest trades
SELECT * FROM k2.silver_trades
ORDER BY exchange_timestamp DESC
LIMIT 10;

-- 5. Check OHLCV data
SELECT * FROM k2.ohlcv_1m
ORDER BY window_start DESC
LIMIT 5;

-- 6. Table sizes
SELECT
    table,
    formatReadableSize(sum(bytes)) as size,
    sum(rows) as rows
FROM system.parts
WHERE database = 'k2' AND active
GROUP BY table
ORDER BY sum(bytes) DESC;
```

---

## Connection Configuration Files

### Create .env file (for Python projects)

```bash
# .env
CLICKHOUSE_HOST=localhost
CLICKHOUSE_HTTP_PORT=8123
CLICKHOUSE_NATIVE_PORT=9000
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=clickhouse
CLICKHOUSE_DATABASE=k2
```

### Python code using .env

```python
import os
from dotenv import load_dotenv
import clickhouse_connect

load_dotenv()

client = clickhouse_connect.get_client(
    host=os.getenv('CLICKHOUSE_HOST'),
    port=int(os.getenv('CLICKHOUSE_HTTP_PORT')),
    username=os.getenv('CLICKHOUSE_USER'),
    password=os.getenv('CLICKHOUSE_PASSWORD'),
    database=os.getenv('CLICKHOUSE_DATABASE')
)
```

---

## Performance Tips

### 1. Use Native Protocol When Possible

Native protocol (port 9000) is faster than HTTP (port 8123):
- **HTTP (8123)**: Good for clickhouse-connect, web clients
- **Native (9000)**: Faster, use with clickhouse-driver or JDBC

### 2. Batch Inserts

```python
# Bad: Row-by-row
for row in rows:
    client.execute('INSERT INTO table VALUES', [row])

# Good: Batch insert
client.insert('table', rows, column_names=columns)
```

### 3. Use Query Parameters

```python
# Prevent SQL injection
client.query(
    'SELECT * FROM silver_trades WHERE canonical_symbol = %(symbol)s',
    parameters={'symbol': 'BTC/USDT'}
)
```

### 4. Limit Result Size

```sql
-- Always use LIMIT for exploratory queries
SELECT * FROM silver_trades LIMIT 1000;

-- Or use date filters
SELECT * FROM silver_trades
WHERE exchange_timestamp >= now() - INTERVAL 1 HOUR;
```

---

## See Also

- [ClickHouse Database Standard](./CLICKHOUSE-DATABASE-STANDARD.md) - Database usage guidelines
- [Data Inspection Guide](./DATA-INSPECTION.md) - Query examples
- [Quick Reference](./QUICK-REFERENCE.md) - Common commands
- [ClickHouse Documentation](https://clickhouse.com/docs/en/integrations/python) - Official Python guide

---

## Summary

**For PyCharm Database Tool**:
```
Host:     localhost
Port:     9000
User:     default
Password: clickhouse
Database: k2
```

**For Python (clickhouse-connect)**:
```python
import clickhouse_connect
client = clickhouse_connect.get_client(
    host='localhost', port=8123,
    username='default', password='clickhouse',
    database='k2'
)
```

**For Python (clickhouse-driver)**:
```python
from clickhouse_driver import Client
client = Client(
    host='localhost', port=9000,
    user='default', password='clickhouse',
    database='k2'
)
```
