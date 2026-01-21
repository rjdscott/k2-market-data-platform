# Step 01: Create Directory Structure

**Phase**: 9 - Demo Materials Consolidation
**Step**: 01 of 06
**Priority**: 🔴 CRITICAL
**Status**: ✅ Complete
**Estimated Time**: 30 minutes
**Actual Time**: 30 minutes
**Dependencies**: None

---

## Objective

Create the foundational `/demos/` directory structure with all subdirectories and placeholder README files. This establishes the organizational framework for consolidating all demo materials.

---

## Tasks

### 1. Create Main Demos Directory
```bash
mkdir -p demos
```

### 2. Create Top-Level Subdirectories
```bash
cd demos
mkdir -p notebooks/exchange-demos
mkdir -p scripts/{execution,utilities,validation,resilience}
mkdir -p docs
mkdir -p reference
```

### 3. Create Placeholder README Files
```bash
touch README.md
touch notebooks/README.md
touch notebooks/exchange-demos/README.md
touch scripts/README.md
touch docs/README.md
touch reference/README.md
```

---

## Expected Directory Structure

After completion, the structure should look like this:

```
demos/
├── README.md                    # Placeholder (will be written in Step 04)
├── notebooks/
│   ├── README.md                # Placeholder (will be written in Step 04)
│   └── exchange-demos/
│       └── README.md            # Placeholder (optional, for context)
├── scripts/
│   ├── README.md                # Placeholder (will be written in Step 04)
│   ├── execution/
│   ├── utilities/
│   ├── validation/
│   └── resilience/
├── docs/
│   └── README.md                # Placeholder (will be written in Step 04)
└── reference/
    └── README.md                # Placeholder (will be written in Step 04)
```

**Total Directories**: 9 (1 root + 8 subdirectories)
**Total Placeholder Files**: 6 README.md files

---

## Verification Steps

### 1. Verify Directory Structure
```bash
tree demos/ -L 2
```

**Expected Output**:
```
demos/
├── README.md
├── notebooks/
│   ├── README.md
│   └── exchange-demos/
│       └── README.md
├── scripts/
│   ├── README.md
│   ├── execution/
│   ├── utilities/
│   ├── validation/
│   └── resilience/
├── docs/
│   └── README.md
└── reference/
    └── README.md
```

### 2. Count Directories
```bash
find demos/ -type d | wc -l
```

**Expected**: 10 directories (including demos/ itself)

### 3. Verify README Files
```bash
find demos/ -name "README.md" -type f
```

**Expected**: 6 README.md files

### 4. Check Permissions
```bash
ls -la demos/
```

**Expected**: All directories and files should be readable and writable

---

## Success Criteria

- [ ] `/demos/` directory exists at project root
- [ ] All 4 top-level subdirectories created (notebooks/, scripts/, docs/, reference/)
- [ ] All 4 script subdirectories created (execution/, utilities/, validation/, resilience/)
- [ ] `notebooks/exchange-demos/` subdirectory created
- [ ] 6 placeholder README.md files created
- [ ] Directory structure verified with `tree` command
- [ ] No errors or warnings during creation

**Completion Score**: 100/100 if all criteria met

---

## Common Issues

### Issue 1: Permission Denied
**Symptom**: Cannot create directories
**Cause**: Insufficient permissions in parent directory
**Fix**:
```bash
# Check current permissions
ls -la .

# If needed, use sudo (development environment only)
sudo mkdir -p demos/
sudo chown $USER:$USER demos/
```

### Issue 2: Directories Already Exist
**Symptom**: `mkdir` reports directories exist
**Cause**: Previous partial migration attempt
**Fix**:
```bash
# Remove existing demos/ directory
rm -rf demos/

# Re-run creation steps
mkdir -p demos/{notebooks/exchange-demos,scripts/{execution,utilities,validation,resilience},docs,reference}
```

### Issue 3: Tree Command Not Found
**Symptom**: `tree: command not found`
**Cause**: Tree utility not installed
**Fix**:
```bash
# macOS
brew install tree

# Ubuntu/Debian
sudo apt-get install tree

# Alternative: Use find
find demos/ -type d
```

---

## Time Tracking

**Estimated**: 30 minutes
**Actual**: 30 minutes
**Notes**: Successfully completed all tasks. Created 10 directories and 6 placeholder README.md files. Structure verified with tree command and matches expected layout exactly. No issues encountered.

---

## Next Step

After completing this step successfully, proceed to:
→ [Step 02: Consolidate Notebooks](./step-02-consolidate-notebooks.md)

---

**Last Updated**: 2026-01-17
**Step Owner**: Implementation Team
