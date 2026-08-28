# Runbook: Reclaim host disk from a bloated Docker.raw

Docker Desktop on Linux stores the VM's whole filesystem in one sparse file,
`~/.docker/desktop/vms/0/data/Docker.raw`. That file grows and **never shrinks on its
own**, so the host can run out of disk while Docker itself reports plenty free. This
recovers the host space without losing images or volumes.

It does not cover disk pressure *inside* the platform (the lake filling the disk is
[lake-disk-usage-high.md](./lake-disk-usage-high.md); ClickHouse and Redpanda bound
themselves), and it does not apply to a plain Docker engine on Linux, which has no VM and
no `Docker.raw`.

**Run every command from the repo root with `set -a && . ./.env && set +a` loaded.**

| # | Failure | MTTR target | Measured |
|---|---------|-------------|----------|
| 1 | Host `/` filling, `Docker.raw` far larger than the VM's real usage | < 15 min | ~3 min, 2026-08-28 (506 GB → 56 GB) |

---

## 1. Docker.raw far larger than what Docker is using

**Symptom**, the host's `/` fills up. `docker system df` accounts for a fraction of it, and
the gap is all in one file.

**Detection**, manual only, no alert covers the host's Docker VM image. Compare the two
directly:

```bash
du -sh ~/.docker/desktop/vms/0/data/Docker.raw
docker system df
```

**Act when `du` exceeds 2× the `docker system df` total.** On 2026-08-28 this host read
**506 GB** for `Docker.raw` against **67 GB** used inside the VM — 7.5×, and 439 GB of host
disk held by nothing.

**Cause**, Docker Desktop attaches the disk to qemu as
`-drive ... format=raw` **without `discard=unmap`**. Blocks freed inside the VM are
therefore never punched out of the host file: the guest's TRIM has nowhere to go. Deleting
images and volumes, and `docker system prune`, all free space inside the VM and none on the
host. This is a property of how the drive is attached, not a leak in the platform.

**Expected behaviour**, nothing self-corrects. The file only ever grows until the host runs
out of space, at which point every write in every container fails at once.

**Recovery**, mount the VM disk on the host and TRIM it from outside. Run these in order.

```bash
# 1. Quit Docker Desktop from its tray icon, then confirm nothing holds the file.
pgrep -af 'qemu|Docker.raw'          # must print nothing before continuing

# 2. Attach the image as a loop device with its partitions exposed.
sudo losetup -fP --show ~/.docker/desktop/vms/0/data/Docker.raw
# -> /dev/loop48

# 3. Check the filesystem read-only first. Do not skip this; if it is dirty, stop
#    and start Docker Desktop again to let the VM replay its journal.
sudo fsck.ext4 -n /dev/loop48p1

# 4. Mount and trim. This is the step that returns space to the host.
sudo mkdir -p /mnt/dockervm
sudo mount /dev/loop48p1 /mnt/dockervm
sudo fstrim -v /mnt/dockervm
# -> /mnt/dockervm: 891.3 GiB trimmed

# 5. Detach cleanly.
sudo umount /mnt/dockervm
sudo losetup -d /dev/loop48

# 6. Verify the host got it back, then start Docker Desktop.
du -sh ~/.docker/desktop/vms/0/data/Docker.raw
```

Substitute the loop device that step 2 actually printed; it will not be `loop48` on your
host. The trimmed figure exceeds the file size because `fstrim` reports the free extents it
discarded across the filesystem, not the bytes reclaimed.

**Measured**, 2026-08-28: `fsck.ext4 -n` reported the filesystem clean, `fstrim` discarded
891.3 GiB, and `du` fell from **506 GB to 56 GB**. Whole sequence **~3 min**, with Docker
Desktop stopped for the duration. The 56 GB that remained is the VM's real content: the
trim reclaims only unallocated space, so images and volumes are untouched.

---

## Warning: do not shrink the disk image to cap growth

Docker Desktop's **Settings → Resources → Disk image size** slider looks like the obvious
way to stop `Docker.raw` from growing again. It is not. On Docker Desktop for Linux,
lowering that value **deletes and recreates the disk image**. Every image, every volume,
every container is destroyed. No confirmation dialog was shown, and no warning appeared in
the UI before or after.

Lowering it from 1 TB to 256 GB on this host produced exactly that. The backend log records
the decision and the recreation, with no intervening prompt:

```
settings changes detected: {"ChangeDataDisk":true ...}
rawdisk: creating ... resizing from 0MiB to 270336MiB
```

`resizing from 0MiB` is the tell: there was nothing to resize, because the old file had
already been removed. The result was total data loss — the whole platform had to be rebuilt
from a fresh clone ([fresh-install.md](./fresh-install.md), ≈15 min).

Raising the slider is safe; lowering it is destructive. If the goal is to reclaim host
disk, use the `fstrim` procedure above, which reclaims the same space with the stack
intact. If the goal really is a smaller cap, treat it as a rebuild: back up any volume you
care about first, and expect to run a fresh install afterwards.

---

## Failure modes / incidents

- **2026-08-28**, `Docker.raw` at 506 GB against 67 GB in use. The `fstrim` procedure
  above ran first and worked (506 GB → 56 GB, stack intact). The disk-image-size slider was
  then lowered to cap regrowth; that wiped every image and volume the trim had just
  preserved.

**Revisit when** `du` on `Docker.raw` again exceeds 2× the `docker system df` total, or
after any Docker Desktop upgrade — check whether the drive is now attached with
`discard=unmap` (`pgrep -af qemu` while Docker is running), which would make this runbook
unnecessary.

**Last verified:** 2026-08-28 on Docker Desktop for Linux, VM 40 GB RAM / 28 CPU.
