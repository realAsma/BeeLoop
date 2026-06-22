unknown_arg() {
  printf '%s: unknown argument %s\n' "${0##*/}" "$1" >&2
  exit 2
}

parse_request() {
  local line in_headers=1

  request=""

  if (($# > 0)); then
    case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    esac
    printf '%s: pass request on stdin\n' "${0##*/}" >&2
    exit 2
  elif [[ ! -t 0 ]]; then
    request="$(cat)"
  else
    prompt=""
    return 0
  fi

  prompt=""

  while IFS= read -r line || [[ -n "$line" ]]; do
    if ((in_headers)); then
      if [[ -z "$line" ]]; then
        in_headers=0
        continue
      fi
      if [[ "$line" == *=* ]]; then
        handle_arg "${line%%=*}" "${line#*=}"
        continue
      fi
      in_headers=0
    fi
    [[ -z "$prompt" ]] || prompt+=$'\n'
    prompt+="$line"
  done <<<"$request"
}
