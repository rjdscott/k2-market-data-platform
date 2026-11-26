# Runbook: Upgrade a pinned image or jar

Use this to move a base image pinned by digest, or a jar baked into an image with its
sha256, to a new release: a Dependabot pull request, an end-of-life notice, a CVE in
something the image ships. The worked example throughout is the Spark rebase of
2026-11-26 (plan 000, step N0-6): `tabulario/spark-iceberg:3.5.5_1.8.1` to the official
`spark:3.5.9-scala2.12-java17-python3-ubuntu`, local tag `k2-spark-iceberg:3.5.9_1.8.1`.
It does not cover putting the new image live: that is a recreate, and recreating a
service is the [operator guide, section 6](../operations/README.md#6-restart-or-recreate-one-service).

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Task | Target | Measured |
|---|------|--------|----------|
| 1 | Find the release, resolve its digest, read what the base gives | < 10 min | not timed (2026-11-26) |
| 2 | Pin the jars that release names, each with a sha256 | < 10 min | not timed (2026-11-26) |
| 3 | Find every place the pin and the tag live | < 5 min | not timed (2026-11-26) |
| 4 | Build under a tag no service runs, and prove it read-only | < 10 min | build 14 s with the base pulled; steps 4 to 8 in 28 s (2026-11-26) |
| 5 | Roll back | < 15 min | **not yet run on this stack** |

---

## 1. Find the release and its digest

**Symptom**, a Dependabot pull request against a Dockerfile under `docker/` or
`services/`, a release note, or an end-of-life date for something the base image ships.
The worked example's trigger: the tabulario base carried Python 3.10.16 and OpenSSL 1.1.1w
on Debian 11, both past their end of life.

**Detection**, manual, or Dependabot's monthly `docker` check over the directories listed in
`.github/dependabot.yml`. No alert covers an ageing base image.

**Expected behaviour**, nothing on the running stack changes until a service is recreated
on an image built from the new Dockerfile. Read section 4's warning before building
anything under a tag `docker-compose.yml` names.

**Recovery**

```bash
# 1. The newest tags in the image's line (Docker Hub API).
curl -fsSL 'https://hub.docker.com/v2/repositories/library/spark/tags?page_size=100&name=scala2.12-java17-python3-ubuntu' \
  | jq -r '.results[].name' | sort -V | tail -4
# 2026-11-26: 3.5.6- ... 3.5.9-scala2.12-java17-python3-ubuntu, so 3.5.9 is the newest 3.5.x.

# 2. Its digest: the OCI image index, which covers every platform. This goes in FROM.
docker buildx imagetools inspect spark:3.5.9-scala2.12-java17-python3-ubuntu | sed -n 3p
# Digest:    sha256:8901b8427694267ed719f27aa1498815dbd3573efb3cde447246fbff29d07272

# 3. What the base runs as and starts, against what the old image did. Every
#    difference is a setting the stack relied on without writing it down.
docker image inspect spark:3.5.9-scala2.12-java17-python3-ubuntu \
  --format '{{.Config.User}} {{.Config.WorkingDir}} {{json .Config.Entrypoint}} {{json .Config.Cmd}}'
# spark /opt/spark/work-dir ["/opt/entrypoint.sh"] null
docker image inspect k2-spark-iceberg:3.5.5_1.8.1 \
  --format '{{.Config.User}} {{.Config.WorkingDir}} {{json .Config.Entrypoint}} {{json .Config.Cmd}}'
#  /opt/spark ["./entrypoint.sh"] ["notebook"]       (empty user: root)

# 4. Jars the running image has that the new base does not: each one is either
#    baked by the Dockerfile or dropped on purpose.
comm -23 <(docker exec k2-spark-iceberg ls /opt/spark/jars | sed -E 's/-[0-9][0-9.]*.*\.jar$//' | sort) \
  <(docker run --rm --entrypoint ls spark:3.5.9-scala2.12-java17-python3-ubuntu /opt/spark/jars \
      | sed -E 's/-[0-9][0-9.]*.*\.jar$//' | sort)
# commons-pool2, iceberg-aws-bundle, iceberg-azure-bundle, iceberg-gcp-bundle,
# iceberg-spark-runtime, kafka-clients, spark-avro_2.12, spark-sql-kafka, spark-token-provider-kafka
```

Write `FROM <name>:<tag>@<digest>`, with the tag kept for reading, and the refresh command
and its date in the comment above it. For the rebase, step 3 is where most of the work
was: the old entrypoint started a Master, a Worker, a History Server, a Thrift server and
Jupyter as root, and its `spark-defaults.conf` held the Iceberg extensions and the event-log
settings. `docker/spark/Dockerfile` now states each of those it keeps, and why it drops the rest.

**Measured**, not timed. Each command returns in seconds.

---

## 2. Pin the jars the release names

**Symptom**, the base moved to a new Spark release and the baked connector jars still carry
the old one. The Dockerfile asserts `pyspark.__version__` at build time, so this is a failed
`docker build`, locally and in the CI `docker` job's spark leg.

**Detection**, the build fails at the `RUN python3 -c "import pyspark, py4j, yaml; ..."` step.

**Expected behaviour**, nothing self-heals: the Kafka source, its token provider and
`spark-avro` are built per Spark release, and `kafka-clients` and `commons-pool2` are not free
choices but what that release's POM declares.

**Recovery**

```bash
# 1. The versions the release declares for the two third-party jars.
curl -fsSL https://repo1.maven.org/maven2/org/apache/spark/spark-sql-kafka-0-10_2.12/3.5.9/spark-sql-kafka-0-10_2.12-3.5.9.pom \
  | grep -A1 -E '<artifactId>(kafka-clients|commons-pool2)</artifactId>'
# kafka-clients 3.4.1, commons-pool2 2.11.1 (2026-11-26)

# 2. A jar's sha256, computed from the same bytes whose published .sha1 matched. Maven
#    Central publishes .sha1 for every jar and .sha256 for only some; one per jar.
mkdir -p /tmp/k2-jar-check && cd /tmp/k2-jar-check \
  && u=https://repo1.maven.org/maven2/org/apache/spark/spark-avro_2.12/3.5.9/spark-avro_2.12-3.5.9.jar \
  && curl -fsSLO "$u" && echo "$(curl -fsSL "$u.sha1")  $(basename "$u")" | sha1sum -c - \
  && sha256sum "$(basename "$u")"; cd - >/dev/null
# spark-avro_2.12-3.5.9.jar: OK
# 9dda7512c1052bcd3774cbe247572cd481d4481eb8a90b7c4865b23a12bbecf8  spark-avro_2.12-3.5.9.jar
rm -r /tmp/k2-jar-check
```

Each `"<maven path> <sha256>"` pair goes in the Dockerfile's jar loop, which checks it with
`sha256sum -c` at build time. Where Maven Central does publish a `.sha256`, compare it too:
for the rebase it matched for the two Iceberg jars and `kafka-clients`.

**Measured**, not timed.

---

## 3. Where the pins live

**Symptom**, a pin moved in one place and not another: CI builds one tag while compose
starts another, or a comment names a version the file no longer uses.

**Detection**, manual. `python3 scripts/check-budget.py` fails a `FROM` without a release
tag or digest and a `pip install` without `==`; it does not compare places with each other.

**Expected behaviour**, the Spark image's pins, all of them:

| Pin | Where |
|-----|-------|
| Base image and digest | `docker/spark/Dockerfile`, `FROM` |
| Iceberg, Kafka and Avro jars, with sha256 | `docker/spark/Dockerfile`, the jar loop |
| PyYAML | `docker/spark/Dockerfile`, `pip install` |
| py4j zip name (moves with Spark) | `docker/spark/Dockerfile`, `PYTHONPATH` |
| Local tag the stack runs | `docker-compose.yml`, `image:` of `spark-iceberg` (and of `lake-ddl` until the v3 lake ingest retired) |
| Local tag CI builds and checks | `.github/workflows/ci.yml`, the `spark` leg's `tag:` and its version step |

**Recovery**

```bash
grep -n -E '^FROM|PyYAML==|py4j-|\.jar [0-9a-f]{64}' docker/spark/Dockerfile
grep -n 'k2-spark-iceberg:' docker-compose.yml .github/workflows/ci.yml
```

Move the `image:` lines in the same branch as the Dockerfile, as their own commit, and merge
the two together: between a Dockerfile change and its `image:` lines, any compose build
writes the new image under the old tag (section 4). The rebase did exactly that, steps N0-6
and N0-13 on one branch.

**Measured**, not timed.

---

## 4. Build under a tag no service runs, and prove it read-only

**Symptom**, a Dockerfile change that has not run a lake job yet.

**Detection**, manual. CI's spark leg builds the image and checks its Python and OpenSSL,
nothing more.

**Expected behaviour**, the candidate runs beside the live stack on its network, reads the
catalog, MinIO and Redpanda, and writes nothing to them.

> **Do not build under the live tag.** `spark-iceberg` has a `build:` section, so compose
> builds this Dockerfile under whatever tag its `image:` line names. With the new
> Dockerfile merged and the `image:` lines not yet moved, `docker compose build`,
> `docker compose up --build` and `make dev-up` (which runs `docker compose up -d --build`)
> build the new image under the old tag and recreate `spark-iceberg` on it: the change goes
> live unproven, and the rollback image loses its tag. Build by hand, under a new tag.

**Recovery**

```bash
# 1. The new tag is not one any container runs (0), and the host has room (at least 6 GiB).
docker ps --format '{{.Image}}' | grep -cx 'k2-spark-iceberg:3.5.9_1.8.1'
awk '/MemAvailable/{printf "%.1f GiB available\n", $2/1048576}' /proc/meminfo

# 2. Build, from a checkout of the branch that carries the change.
docker build -f docker/spark/Dockerfile -t k2-spark-iceberg:3.5.9_1.8.1 .

# 3. CI's check: Python 3.10, OpenSSL 3.
docker run --rm --network none k2-spark-iceberg:3.5.9_1.8.1 \
  python3 -c "import sys, ssl; print(sys.version, ssl.OPENSSL_VERSION)"
# 3.10.12 (main, Aug 31 2026, 10:18:17) [GCC 11.4.0] OpenSSL 3.0.2 15 Mar 2022

# 4. A candidate container on the stack's network, with the lake scripts read-only and the
#    credentials compose gives spark-iceberg. The network is the compose project's:
#    `docker network ls --filter name=k2-net` prints its name.
docker run -d --name k2-spark-candidate --network k2-market-data-platform_k2-net \
  --cpus 2 --cpuset-cpus 0-11 --memory 3g -e MINIO_ROOT_USER -e MINIO_ROOT_PASSWORD \
  -v "$PWD/docker/lake":/home/iceberg/lake:ro \
  -v "$PWD/config/instruments.yaml":/home/iceberg/config/instruments.yaml:ro \
  -v "$PWD/config/bars.yaml":/home/iceberg/config/bars.yaml:ro \
  k2-spark-iceberg:3.5.9_1.8.1

# 5. The Kafka jars. Not runnable until phase 004: the probe used on 2026-11-26 was the v3
#    lake ingest's own (`ingest.py --probe`, one record per topic, writing nothing), archived
#    in legacy/v3-lake-ingest/ with that pipeline; phase 004's Kafka connector brings the next.
#    On 2026-11-26 it printed one "schema id N, N byte record" line per topic carrying traffic
#    and exited 0.

# 6. The catalog and S3 jars: one row of an existing table through Lakekeeper and MinIO.
#    Not runnable until phase 002 puts a table in the lake: on 2026-11-26 the table read was
#    one of the v3 lake's, since dropped. Name an existing table in place of <namespace>.<table>.
docker exec -i k2-spark-candidate python3 - <<'PY'
import sys; sys.path.insert(0, "/home/iceberg/lake")
from spark_conf import lake_session
spark = lake_session("k2-spark-candidate", "768m")
print("rows read:", len(spark.table("lake.<namespace>.<table>").limit(1).collect()))
spark.stop()
PY
# rows read: 1

# 7. The History Server (the image's CMD, and the event-log cleaner) is up, and tini has
#    reaped every job's leftover JVM (0 zombies).
docker exec k2-spark-candidate curl -s localhost:18080/api/v1/version
docker exec k2-spark-candidate sh -c 'ps -eo stat= | grep -c "^Z"'

# 8. Remove it.
docker rm -f k2-spark-candidate
```

Then the change goes live: move the `image:` line to the new tag and recreate
`spark-iceberg` when idle, with the
[operator guide's pattern](../operations/README.md#the-pattern-wait-for-idle-recreate-verify),
and run `make gates` on it. Build the new tag first: with the `image:` line moved,
`docker compose build spark-iceberg` writes only the new tag and leaves the old one, the
rollback, alone.

**Measured**, 2026-11-26: the build took 14 s with the base image already pulled; steps 4
to 8 took 28 s together, with a scheduled ingest running in `k2-spark-iceberg` beside them.

---

## 5. Roll back

**Symptom**, a Spark job fails on the new image after it went live.

**Detection**, `AirflowTaskFailed` once phase 002 schedules a job; until then, the job's own
output (the lake alerts that also caught this retired with the v3 lake).

**Expected behaviour**, the previous image is still on the host under its old tag, as long
as nothing rebuilt that tag (section 4's warning).

**Recovery**

```bash
# The old tag is still present (both lines printed).
docker image ls k2-spark-iceberg --format '{{.Tag}} {{.ID}} {{.CreatedSince}}'
```

Revert the commit that moved the `image:` line, then recreate `spark-iceberg` with the
[operator guide's pattern](../operations/README.md#the-pattern-wait-for-idle-recreate-verify).
If the old tag is gone, check out the commit before the Dockerfile change and build it under
the old tag by hand, as in section 4 step 2.

**Measured**, not yet run on this stack: no rollback has been needed or induced.

---

## Failure modes / incidents

- **2026-11-26**, the worked example. Three things the commands above already allow for:
  the stack's network is `k2-market-data-platform_k2-net`, not the `k2-net` compose names
  it; Docker Desktop refuses a bind mount from `/tmp`, so a proof script goes in on stdin
  (`docker exec -i ... python3 -`); and the image's History Server exits at start under
  `--network none` (`UnknownHostException` on its own hostname), so only a one-off command,
  never the default CMD, runs without a network.

---

**Last verified:** 2026-11-26 against branch `step/n0-6-spark-rebase`, with the live stack
still on `k2-spark-iceberg:3.5.5_1.8.1`.
