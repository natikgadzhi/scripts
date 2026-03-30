#!/usr/bin/env bash
set -uo pipefail

BASE_DIR="${1:-$HOME/src/natikgadzhi}"

# Collect all git repos recursively
declare -a repos=()
declare -a dirs=()
while IFS= read -r gitdir; do
    dir="$(dirname "$gitdir")"
    # Show path relative to BASE_DIR for readability
    repos+=("${dir#$BASE_DIR/}")
    dirs+=("$dir")
done < <(find "$BASE_DIR" -name .git -type d 2>/dev/null | grep -v '\.build' | sort)

total=${#repos[@]}
if [ "$total" -eq 0 ]; then
    echo "No git repositories found in $BASE_DIR"
    exit 0
fi

# Status arrays
declare -a statuses=()
declare -a details=()
for ((i = 0; i < total; i++)); do
    statuses+=("PENDING")
    details+=("")
done

# Colors
RESET=$'\033[0m'
BOLD=$'\033[1m'
DIM=$'\033[2m'
GREEN=$'\033[32m'
YELLOW=$'\033[33m'
RED=$'\033[31m'
CYAN=$'\033[36m'
BLUE=$'\033[34m'

# Total lines drawn by draw_table: header + separator + total repos + blank + progress = total + 4
TABLE_LINES=$((total + 4))

draw_table() {
    local current_op="${1:-}"
    local completed="${2:-0}"

    # On redraw, move cursor back to start of table
    if [ "${first_draw}" -eq 0 ]; then
        printf '\033[%dA\r' "$TABLE_LINES"
    fi
    first_draw=0

    # Header
    printf '\033[2K'"${BOLD}%-30s %-10s %s${RESET}"'\n' "REPO" "STATUS" "DETAIL"
    printf '\033[2K%-30s %-10s %s\n' "────" "──────" "──────"

    # Rows
    local j
    for ((j = 0; j < total; j++)); do
        local status_text="${statuses[$j]}"
        local detail_text="${details[$j]}"
        local colored_status
        case "$status_text" in
            PENDING) colored_status="${DIM}PENDING${RESET}" ;;
            OK)      colored_status="${GREEN}OK${RESET}" ;;
            UPDATED) colored_status="${GREEN}${BOLD}UPDATED${RESET}" ;;
            SKIP)    colored_status="${YELLOW}SKIP${RESET}" ;;
            FAILED)  colored_status="${RED}FAILED${RESET}" ;;
            *)       colored_status="$status_text" ;;
        esac
        local pad=$((10 - ${#status_text}))
        printf '\033[2K%-30s %b%-*s %s\n' "${repos[$j]}" "$colored_status" "$pad" "" "$detail_text"
    done

    # Progress bar
    local bar_width=40
    local filled=$((total > 0 ? completed * bar_width / total : 0))
    local empty=$((bar_width - filled))
    local bar=""
    local b
    for ((b = 0; b < filled; b++)); do bar+="█"; done
    for ((b = 0; b < empty; b++)); do bar+="░"; done

    local op_text=""
    if [ -n "$current_op" ]; then
        op_text="  ${BLUE}>${RESET} ${current_op}"
    fi

    printf '\033[2K\n'
    printf '\033[2K %b[%b%b%b]%b %d/%d%b\033[K\n' \
        "$CYAN" "${GREEN}${bar:0:$filled}${RESET}" "${DIM}" "${bar:$filled}${RESET}" "$CYAN" \
        "$completed" "$total" "$op_text"
}

first_draw=1
draw_table "" 0

# Process each repo
completed=0
for ((i = 0; i < total; i++)); do
    repo="${repos[$i]}"
    dir="${dirs[$i]}"

    # Determine remote
    draw_table "${repo}: checking remotes..." "$completed"
    remote=""
    if git -C "$dir" remote | grep -q '^origin$'; then
        remote="origin"
    elif git -C "$dir" remote | grep -q '^upstream$'; then
        remote="upstream"
    else
        statuses[$i]="SKIP"
        details[$i]="no origin or upstream remote"
        completed=$((completed + 1))
        continue
    fi

    # Get the current branch
    branch="$(git -C "$dir" symbolic-ref --short HEAD 2>/dev/null || true)"
    if [ -z "$branch" ]; then
        statuses[$i]="SKIP"
        details[$i]="detached HEAD"
        completed=$((completed + 1))
        continue
    fi

    # Fetch
    draw_table "${repo}: fetching ${remote}/${branch}..." "$completed"
    if output=$(git -C "$dir" fetch "$remote" "$branch" 2>&1); then
        # Merge
        draw_table "${repo}: merging ${remote}/${branch}..." "$completed"
        if ff_output=$(git -C "$dir" merge --ff-only "${remote}/${branch}" 2>&1); then
            if echo "$ff_output" | grep -q "Already up to date"; then
                statuses[$i]="OK"
                details[$i]="already up to date"
            else
                statuses[$i]="UPDATED"
                details[$i]="fast-forwarded ${remote}/${branch}"
            fi
        else
            statuses[$i]="FAILED"
            details[$i]="ff merge failed: $(echo "$ff_output" | head -1)"
        fi
    else
        statuses[$i]="FAILED"
        details[$i]="fetch failed: $(echo "$output" | head -1)"
    fi

    completed=$((completed + 1))
done

# Final draw
draw_table "done" "$completed"
echo ""
