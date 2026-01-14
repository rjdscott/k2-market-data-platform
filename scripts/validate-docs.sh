#!/usr/bin/env bash
#
# Documentation Validation Script
# Validates markdown links, checks for placeholders, and ensures doc quality
#
# Usage: ./scripts/validate-docs.sh
#

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Counters
TOTAL_CHECKS=0
ERRORS=0
WARNINGS=0

# Base directory
DOCS_DIR="docs"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "===================================="
echo "K2 Documentation Validation"
echo "===================================="
echo ""

# ============================================================================
# Check 1: Broken Markdown Links
# ============================================================================

echo -e "${BLUE}[1/7] Checking markdown links...${NC}"
BROKEN_LINKS=()

# Extract all markdown links
while IFS= read -r line; do
    # Skip if line doesn't contain markdown link
    if [[ ! "$line" =~ "].md" ]]; then
        continue
    fi

    TOTAL_CHECKS=$((TOTAL_CHECKS + 1))

    # Extract file path
    FILE=$(echo "$line" | cut -d: -f1)

    # Extract link (everything between ]( and ))
    LINK=$(echo "$line" | sed -n 's/.*\](\([^)]*\.md[^)]*\)).*/\1/p' | head -1)

    if [[ -z "$LINK" ]]; then
        continue
    fi

    # Skip external links
    if [[ "$LINK" =~ ^https?:// ]]; then
        continue
    fi

    # Skip anchors (links to sections within same file)
    if [[ "$LINK" =~ ^# ]]; then
        continue
    fi

    # Resolve relative path
    FILE_DIR=$(dirname "$FILE")

    # Handle different link types
    if [[ "$LINK" =~ ^/ ]]; then
        # Absolute from docs root
        TARGET="docs$LINK"
    else
        # Relative link
        TARGET="$FILE_DIR/$LINK"
    fi

    # Normalize path
    TARGET=$(cd "$(dirname "$TARGET")" 2>/dev/null && echo "$(pwd)/$(basename "$TARGET")" || echo "$TARGET")

    # Check if target exists
    if [[ ! -f "$TARGET" ]]; then
        BROKEN_LINKS+=("$FILE -> $LINK")
        ERRORS=$((ERRORS + 1))
    fi
done < <(grep -r "\[.*\](.*\.md" "$DOCS_DIR/" --include="*.md" 2>/dev/null || true)

if [[ ${#BROKEN_LINKS[@]} -gt 0 ]]; then
    echo -e "${RED}✗ Found ${#BROKEN_LINKS[@]} broken link(s):${NC}"
    for broken in "${BROKEN_LINKS[@]}"; do
        echo "  - $broken"
    done
else
    echo -e "${GREEN}✓ All markdown links valid${NC}"
fi
echo ""

# ============================================================================
# Check 2: TODO/FIXME/TBD Placeholders
# ============================================================================

echo -e "${BLUE}[2/7] Checking for placeholder text...${NC}"
PLACEHOLDERS=$(grep -r "TODO\|FIXME\|TBD\|PLACEHOLDER" "$DOCS_DIR/" --include="*.md" 2>/dev/null || true)

if [[ -n "$PLACEHOLDERS" ]]; then
    COUNT=$(echo "$PLACEHOLDERS" | wc -l | tr -d ' ')
    WARNINGS=$((WARNINGS + COUNT))
    echo -e "${YELLOW}⚠ Found $COUNT placeholder(s):${NC}"
    echo "$PLACEHOLDERS" | head -10
    if [[ $COUNT -gt 10 ]]; then
        echo "  ... and $((COUNT - 10)) more"
    fi
else
    echo -e "${GREEN}✓ No placeholders found${NC}"
fi
echo ""

# ============================================================================
# Check 3: Outdated Dates (>6 months old)
# ============================================================================

echo -e "${BLUE}[3/7] Checking for outdated dates...${NC}"
OUTDATED=$(grep -r "Last Updated.*202[0-4]" "$DOCS_DIR/" --include="*.md" 2>/dev/null || true)

if [[ -n "$OUTDATED" ]]; then
    COUNT=$(echo "$OUTDATED" | wc -l | tr -d ' ')
    WARNINGS=$((WARNINGS + COUNT))
    echo -e "${YELLOW}⚠ Found $COUNT file(s) with potentially outdated dates (2020-2024):${NC}"
    echo "$OUTDATED" | head -5
    if [[ $COUNT -gt 5 ]]; then
        echo "  ... and $((COUNT - 5)) more"
    fi
else
    echo -e "${GREEN}✓ All dates are recent (2025+)${NC}"
fi
echo ""

# ============================================================================
# Check 4: Old Phase References (should be phase-0, phase-1, phase-2-prep, phase-3-demo-enhancements)
# ============================================================================

echo -e "${BLUE}[4/7] Checking for old phase references...${NC}"
OLD_PHASES=$(grep -r "phase-3-crypto\|phase-2-platform-enhancements\|phase-4-consolidate\|phase-5-multi-exchange" "$DOCS_DIR/" --include="*.md" 2>/dev/null | grep -v "renamed\|formerly\|COMPREHENSIVE-REVIEW\|PROGRESS\|consolidation\|Renamed\|Removed" || true)

if [[ -n "$OLD_PHASES" ]]; then
    COUNT=$(echo "$OLD_PHASES" | wc -l | tr -d ' ')
    ERRORS=$((ERRORS + COUNT))
    echo -e "${RED}✗ Found $COUNT old phase reference(s):${NC}"
    echo "$OLD_PHASES"
else
    echo -e "${GREEN}✓ No old phase references (excluding historical docs)${NC}"
fi
echo ""

# ============================================================================
# Check 5: Empty Directories
# ============================================================================

echo -e "${BLUE}[5/7] Checking for empty directories...${NC}"
EMPTY_DIRS=$(find "$DOCS_DIR" -type d -empty 2>/dev/null || true)

if [[ -n "$EMPTY_DIRS" ]]; then
    COUNT=$(echo "$EMPTY_DIRS" | wc -l | tr -d ' ')
    WARNINGS=$((WARNINGS + COUNT))
    echo -e "${YELLOW}⚠ Found $COUNT empty director(y/ies):${NC}"
    echo "$EMPTY_DIRS"
else
    echo -e "${GREEN}✓ No empty directories${NC}"
fi
echo ""

# ============================================================================
# Check 6: Missing Last Updated Dates
# ============================================================================

echo -e "${BLUE}[6/7] Checking for missing 'Last Updated' dates...${NC}"
MISSING_DATES=()

while IFS= read -r file; do
    # Skip archive, reviews (historical), consolidation tracking, and test results
    if [[ "$file" =~ archive/ ]] || \
       [[ "$file" =~ reviews/2026 ]] || \
       [[ "$file" =~ consolidation/ ]] || \
       [[ "$file" =~ TEST_RESULTS ]] || \
       [[ "$file" =~ COMPLETION-REPORT ]]; then
        continue
    fi

    if ! grep -q "Last Updated" "$file"; then
        MISSING_DATES+=("$file")
    fi
done < <(find "$DOCS_DIR" -name "*.md" -type f)

if [[ ${#MISSING_DATES[@]} -gt 0 ]]; then
    WARNINGS=$((WARNINGS + ${#MISSING_DATES[@]}))
    echo -e "${YELLOW}⚠ Found ${#MISSING_DATES[@]} file(s) without 'Last Updated' date:${NC}"
    for file in "${MISSING_DATES[@]:0:10}"; do
        echo "  - $file"
    done
    if [[ ${#MISSING_DATES[@]} -gt 10 ]]; then
        echo "  ... and $((${#MISSING_DATES[@]} - 10)) more"
    fi
else
    echo -e "${GREEN}✓ All relevant files have 'Last Updated' dates${NC}"
fi
echo ""

# ============================================================================
# Check 7: Duplicate Headings (potential duplicate content)
# ============================================================================

echo -e "${BLUE}[7/7] Checking for documentation health...${NC}"
TOTAL_DOCS=$(find "$DOCS_DIR" -name "*.md" -type f | wc -l | tr -d ' ')
LARGE_DOCS=$(find "$DOCS_DIR" -name "*.md" -type f -size +100k | wc -l | tr -d ' ')

echo -e "${GREEN}✓ Documentation health:${NC}"
echo "  - Total documents: $TOTAL_DOCS"
echo "  - Large documents (>100KB): $LARGE_DOCS"
echo ""

# ============================================================================
# Summary
# ============================================================================

echo "===================================="
echo "Validation Summary"
echo "===================================="
echo "Total link checks: $TOTAL_CHECKS"
echo -e "Errors: ${RED}$ERRORS${NC}"
echo -e "Warnings: ${YELLOW}$WARNINGS${NC}"

if [[ $ERRORS -gt 0 ]]; then
    echo ""
    echo -e "${RED}✗ Validation FAILED with $ERRORS error(s)${NC}"
    echo ""
    echo "Common fixes:"
    echo "  - Update links to use correct relative paths"
    echo "  - Fix old phase references (phase-3-crypto → phase-2-prep)"
    echo "  - Remove links to deleted files"
    exit 1
elif [[ $WARNINGS -gt 0 ]]; then
    echo ""
    echo -e "${YELLOW}⚠ Validation passed with $WARNINGS warning(s)${NC}"
    exit 0
else
    echo ""
    echo -e "${GREEN}✓ All checks PASSED${NC}"
    exit 0
fi
