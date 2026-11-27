# Sourced by the bash scripts that write under ${K2_SECRETS} (scripts/tls.sh, scripts/alerts.sh
# install-webhook); scripts/principal-secret.py makes the same test in Python (checkout_above).
#
#   outside_checkout <path> <name>   exits 2, saying why as <name>, unless <path> is outside every
#                                    git checkout
#
# A checkout is found by a `.git` (a directory, or a worktree's file) at or above the path's
# nearest existing directory, never by asking git: git refuses a repository another uid owns
# ("dubious ownership") and prints nothing, which must not read as "not a checkout". A path whose
# nearest existing directory cannot be entered is refused too: what cannot be looked at is not
# known to be outside.
outside_checkout() {
  local path=$1 name=$2 d=$1
  while [ ! -e "$d" ] && [ ! -L "$d" ]; do d=$(dirname "$d"); done
  if ! d=$(cd "$d" 2>/dev/null && pwd -P); then
    echo "$name: cannot look into the nearest existing directory of $path; refused" >&2
    exit 2
  fi
  while :; do
    if [ -e "$d/.git" ] || [ -L "$d/.git" ]; then
      echo "$name: $path is inside a git checkout ($d); K2_SECRETS must name a directory outside any" >&2
      exit 2
    fi
    [ "$d" = / ] && return 0
    d=$(dirname "$d")
  done
}
