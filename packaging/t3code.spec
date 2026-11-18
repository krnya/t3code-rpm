%global debug_package %{nil}
%global source_date_epoch_from_changelog 0
# The Electron distribution is already stripped. Rewriting files can damage its
# snapshots and native modules. Keep the normal RPM dependency generators.
%global __brp_strip %{nil}
%global __brp_strip_comment_note %{nil}
%global __brp_strip_static_archive %{nil}
%global __brp_strip_lto %{nil}
# These are private Electron libraries, not system-wide ELF providers.
%global __provides_exclude_from ^%{_libdir}/t3code/.*$
%global __requires_exclude ^(lib(ffmpeg|EGL|GLESv2|vk_swiftshader|ggml[^.]*|transcribe|fff_c)[.]so([.]1)?[(])

Name:           t3code
Version:        @VERSION@
Release:        @REVISION@%{?dist}
Summary:        Desktop GUI for coding agents
License:        MIT AND BSD-2-Clause AND BSD-3-Clause AND Apache-2.0 AND ISC AND OFL-1.1 AND CC0-1.0 AND LGPL-2.1-or-later AND MPL-2.0 AND Unlicense
URL:            https://github.com/pingdotgg/t3code
Source0:        t3code-@COMMIT@.tar.gz
Source1:        build.sh
Source2:        t3code
Source3:        t3code.desktop
Source4:        upstream.json
Source5:        toolchain.json
Source6:        desktop-pnpm-lock.yaml
Source7:        prepare-workspace.py
Source8:        electron-linux-x64.zip
Source9:        ffmpeg-corresponding-source.tar.gz
Source10:       FFmpeg-NOTICE
ExclusiveArch:  x86_64

BuildRequires:  binutils
BuildRequires:  cargo
BuildRequires:  curl
BuildRequires:  desktop-file-utils
BuildRequires:  gcc
BuildRequires:  gcc-c++
BuildRequires:  git-core
BuildRequires:  ImageMagick
BuildRequires:  libsecret-devel
BuildRequires:  make
BuildRequires:  pkgconf-pkg-config
BuildRequires:  python3
BuildRequires:  python3-pyyaml
BuildRequires:  rust
BuildRequires:  unzip
BuildRequires:  wayland-devel
BuildRequires:  which
BuildRequires:  xz
BuildRequires:  /usr/bin/readelf
BuildRequires:  gtk3
BuildRequires:  nss
BuildRequires:  alsa-lib
BuildRequires:  mesa-libgbm
Requires:       git-core
Requires:       gtk3
Requires:       hicolor-icon-theme
Requires:       libnotify
Requires:       libsecret
Requires:       nss
Requires:       nss-softokn
Requires:       xdg-utils
Recommends:     xdg-desktop-portal

%description
T3 Code provides a graphical workspace for coding agents. It bundles the
upstream Electron distribution, the local T3 Code server and the web client.

%prep
echo '@SOURCE_SHA@  %{SOURCE0}' | sha256sum --check --strict
echo '@ELECTRON_RPM_SHA@  %{SOURCE8}' | sha256sum --check --strict
echo '@FFMPEG_SOURCE_SHA@  %{SOURCE9}' | sha256sum --check --strict
%setup -q -n t3code-@COMMIT@

%build
export SOURCE_DATE_EPOCH=@SOURCE_DATE_EPOCH@
export T3_BUILD_JOBS=%{_smp_build_ncpus}
bash %{SOURCE1} "$PWD" "$PWD/rpm-output" %{SOURCE4} %{SOURCE5} %{SOURCE6} %{SOURCE7} %{SOURCE8}

%install
install -d %{buildroot}%{_libdir}/t3code
cp -a rpm-output/linux-unpacked/. %{buildroot}%{_libdir}/t3code/
chmod -R u=rwX,go=rX %{buildroot}%{_libdir}/t3code
chmod 4755 %{buildroot}%{_libdir}/t3code/chrome-sandbox
install -Dm0755 %{SOURCE2} %{buildroot}%{_bindir}/t3code
desktop-file-install --dir=%{buildroot}%{_datadir}/applications %{SOURCE3}
install -Dm0644 apps/desktop/resources/linux/com.t3tools.t3code.metainfo.xml \
    %{buildroot}%{_datadir}/metainfo/com.t3tools.t3code.metainfo.xml
for icon in rpm-output/icons/*.png; do
    size=$(basename "$icon" .png)
    install -Dm0644 "$icon" "%{buildroot}%{_datadir}/icons/hicolor/${size}/apps/t3code.png"
done
install -Dm0644 %{SOURCE4} %{buildroot}%{_docdir}/t3code/upstream.json
install -Dm0644 %{SOURCE5} %{buildroot}%{_docdir}/t3code/toolchain.json
install -Dm0644 rpm-output/desktop-pnpm-lock.yaml %{buildroot}%{_docdir}/t3code/desktop-pnpm-lock.yaml
install -d %{buildroot}%{_licensedir}/t3code
mv %{buildroot}%{_libdir}/t3code/LICENSE* %{buildroot}%{_licensedir}/t3code/
cp -a LICENSE third-party-licenses %{buildroot}%{_licensedir}/t3code/
install -m0644 %{SOURCE10} %{buildroot}%{_licensedir}/t3code/FFmpeg-NOTICE

%check
test -s %{buildroot}%{_libdir}/t3code/resources/app.asar
test -x %{buildroot}%{_libdir}/t3code/t3code
test "$(stat -c %%a %{buildroot}%{_libdir}/t3code/chrome-sandbox)" = 4755
desktop-file-validate %{buildroot}%{_datadir}/applications/t3code.desktop

%files
%license %{_licensedir}/t3code
%doc %{_docdir}/t3code
%{_bindir}/t3code
%dir %{_libdir}/t3code
%{_libdir}/t3code/*
%{_datadir}/applications/t3code.desktop
%{_datadir}/metainfo/com.t3tools.t3code.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/t3code.png
