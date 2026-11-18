#!/usr/bin/env bash
set -euo pipefail

if [[ $# != 4 || ( $1 != build && $1 != promote ) ]]; then
    echo 'Usage: publish-copr.sh build|promote OWNER/t3code VERIFIED_BUILD RELEASE_OUTPUT' >&2
    exit 2
fi
mode=$1
project=$2
verified=$(realpath "$3")
out=$(realpath -m "$4")
chroot=fedora-45-x86_64
api=https://copr.fedorainfracloud.org/api_3
[[ $project =~ ^@?[A-Za-z0-9_.-]+/t3code$ ]]
owner=${project%/*}
query="ownername=$owner&projectname=t3code"
magic='https://download.copr.fedorainfracloud.org/'
: "${COPR_CONFIG:?Set the COPR_CONFIG GitHub secret}"
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
(umask 077; printf '%s\n' "$COPR_CONFIG" > "$scratch/config")
unset COPR_CONFIG
# Use only the Fedora service, even if credentials were pasted from another host.
python3 - "$scratch/config" <<'PY'
import configparser
import sys
config = configparser.ConfigParser(interpolation=None)
config.read(sys.argv[1])
assert config["copr-cli"]["copr_url"].rstrip("/") == "https://copr.fedorainfracloud.org"
PY
copr=(copr-cli --config "$scratch/config")
get() {
    local url=$1 timeout=${2:-90} status
    curl -fsSL --proto '=https' --proto-redir '=https' --connect-timeout 20 \
        --retry 3 --max-time "$timeout" "$url" || {
        status=$?
        printf 'COPR request failed for %s\n' "$url" >&2
        return "$status"
    }
}
copr_data=$(get "$api/project?$query")
repo=$(jq -er --arg chroot "$chroot" '.chroot_repos[$chroot]' <<<"$copr_data")
[[ $repo == "$magic"* ]] || { echo 'Unexpected COPR download host' >&2; exit 1; }
if ! jq -e '.devel_mode == true and .enable_net == true and .additional_repos == []' <<<"$copr_data" >/dev/null; then
    echo 'Configure COPR for manual repository regeneration, build network access and standard Fedora repos.' >&2
    exit 1
fi

mkdir -p "$out"
(cd "$verified" && sha256sum -c --strict SHA256SUMS)
ffmpeg_source_sha=$(jq -er 'select(.copr_profile == 1).ffmpeg_source_sha256' "$verified/toolchain.json")
[[ $ffmpeg_source_sha =~ ^[0-9a-f]{64}$ ]]
echo "$ffmpeg_source_sha  $verified/ffmpeg-corresponding-source.tar.gz" | sha256sum -c --strict
version=$(jq -er .version "$verified/upstream.json")
revision=$(jq -er .revision "$verified/upstream.json")
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ && $revision =~ ^[1-9][0-9]*$ ]]
evr="$version-$revision.fc45"
srpm="$verified/t3code-$evr.src.rpm"
rpmname="t3code-$evr.x86_64"
[[ -s $srpm ]]

rpm_location() {
    local repository=$1 locations location
    locations=$(dnf -q --refresh --repofrompath="t3code-verify,$repository" --repo=t3code-verify \
        repoquery --location "$rpmname" 2>/dev/null) || return 1
    while IFS= read -r location; do
        [[ $location == "$magic"* && $location == */"$rpmname.rpm" ]] || continue
        printf '%s\n' "$location"
        return 0
    done <<<"$locations"
    return 1
}

if [[ $mode == promote ]]; then
    [[ -s "$out/$rpmname.rpm" && -s "$out/copr-build.json" ]]
    id=$(jq -er .id "$out/copr-build.json")
    [[ $id =~ ^[0-9]+$ ]]
    package_in_repo() {
        local location
        location=$(rpm_location "$repo") || return 1
        get "$location" 600 > "$scratch/live.rpm" && cmp -s "$scratch/live.rpm" "$out/$rpmname.rpm"
    }
    if ! package_in_repo; then
        "${copr[@]}" regenerate-repos "$project"
        for ((attempt = 0; attempt < 30; attempt++)); do
            if package_in_repo; then break; fi
            sleep 20
        done
        (( attempt < 30 )) || { echo "COPR repository did not publish build $id" >&2; exit 1; }
    fi
    echo "Verified COPR repository publication for build $id"
    exit 0
fi

builds() {
    local offset=0 page count
    while :; do
        page=$(get "$api/build/list/?$query&order_type=DESC&limit=100&offset=$offset") || return
        jq -c '.items[]' <<<"$page" || return
        count=$(jq -er '.items | length' <<<"$page") || return
        (( count == 100 )) || break
        offset=$(( offset + count ))
    done
}

# The upload happens at most once while an existing attempt is in flight.
# An unresolved source entry may be a recent upload whose metadata is pending.
history=$(builds)
matches=$(jq -s --arg version "$evr" --arg plain "$version-$revision" --arg chroot "$chroot" '
    [.[] | select(.projectname == "t3code" and (.chroots | index($chroot)))
        | select(.source_package.name == "t3code" and
                (.source_package.version == $version or .source_package.version == $plain))
        | select(.state != "failed" and .state != "canceled" and .state != "skipped")]
' <<<"$history")
if [[ $(jq 'length' <<<"$matches") -gt 1 ]]; then
    echo "Multiple COPR builds match $rpmname. Reconcile them before publishing." >&2
    exit 1
fi
id=$(jq -r '.[0].id // empty' <<<"$matches")
if [[ -z $id ]]; then
    if jq -es 'any(.[]; (.source_package.name == null or .source_package.version == null)
                     and (.state != "failed" and .state != "canceled" and .state != "skipped"
                          and .state != "succeeded"))' <<<"$history" >/dev/null; then
        echo 'A COPR upload is still resolving. Retry after it has source metadata.' >&2
        exit 1
    fi
    submitted=$("${copr[@]}" build --nowait --chroot "$chroot" --timeout 5400 --enable-net on "$project" "$srpm")
    printf '%s\n' "$submitted"
    id=$(sed -nE 's/^Created builds: ([0-9]+)$/\1/p' <<<"$submitted")
fi
[[ $id =~ ^[0-9]+$ ]] || { echo 'COPR did not return a build ID' >&2; exit 1; }
echo "Monitoring https://copr.fedorainfracloud.org/coprs/build/$id/"
for ((attempt = 0; attempt < 120; attempt++)); do
    build=$(get "$api/build/$id")
    state=$(jq -er .state <<<"$build")
    case "$state" in
        succeeded) break ;;
        failed|canceled|skipped)
            echo "COPR build $id ended in state $state" >&2
            exit 1
            ;;
    esac
    sleep 60
done
[[ $state == succeeded ]] || { echo "Timed out waiting for COPR build $id" >&2; exit 1; }

source_url=$(jq -er .source_package.url <<<"$build")
[[ $source_url == "$magic"* ]]
get "$source_url" 600 > "$scratch/source.rpm"
digest_format='%{SHA256HEADER}:%{PAYLOADSHA256}'
local_digests=$(rpm -qp --qf "$digest_format" "$srpm")
[[ $local_digests =~ ^[0-9a-f]{64}:[0-9a-f]{64}$ ]]
[[ $(rpm -qp --qf "$digest_format" "$scratch/source.rpm") == "$local_digests" ]] || {
    echo "COPR build $id used another source RPM for $evr" >&2
    exit 1
}
result=$(get "$api/build-chroot?build_id=$id&chrootname=$chroot")
jq -e '.state == "succeeded"' <<<"$result" >/dev/null
# Pulp can publish the candidate to devel without a download at result_url.
# Also check the public repo when resuming after metadata promotion.
echo "Locating the signed RPM for COPR build $id"
for ((attempt = 0; attempt < 12; attempt++)); do
    if rpmurl=$(rpm_location "${repo%/}/devel/") || rpmurl=$(rpm_location "$repo"); then
        if get "$rpmurl" 600 > "$scratch/candidate.rpm"; then break; fi
    fi
    sleep 10
done
(( attempt < 12 )) || { echo "COPR build $id has no downloadable RPM in repository metadata" >&2; exit 1; }
get "${repo%/$chroot/}/pubkey.gpg" > "$scratch/pubkey.gpg"
mkdir "$scratch/rpmdb"
rpm --dbpath "$scratch/rpmdb" --import "$scratch/pubkey.gpg"
signature=$(rpm --dbpath "$scratch/rpmdb" -Kv "$scratch/candidate.rpm")
printf '%s\n' "$signature"
grep -Ei 'signature.*: OK' <<<"$signature"
[[ $(rpm --dbpath "$scratch/rpmdb" -qp --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}' \
    "$scratch/candidate.rpm") == "$rpmname" ]]
mv -- "$scratch/candidate.rpm" "$out/$rpmname.rpm"
jq -n --argjson build "$build" --argjson chroot "$result" --arg repo "$repo" \
    '$build + {chroot_result: $chroot, repository_url: $repo}' > "$out/copr-build.json"
