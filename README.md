# T3 Code for Fedora

An unofficial RPM distribution of [T3 Code](https://github.com/pingdotgg/t3code) for **Fedora 45 x86_64**. The package includes the native Electron desktop app, its local server, and the Linux helpers. T3 Tools Inc. maintains the upstream application.

## Install

Install from COPR once the first release is available.

```bash
sudo dnf copr enable krnya/t3code
sudo dnf install t3code
```

If DNF cannot find the `copr` command, install `dnf5-plugins` first. Open **T3 Code** from the application menu or run `t3code` from your desktop session. The app supports Wayland and X11.

## Updates

GitHub Actions checks upstream for stable releases every six hours. It builds each selected release for Fedora, tests the RPM, and submits the source RPM to COPR. The signed COPR package becomes available to DNF after it passes a second set of tests. You can follow the run in **Actions** and find the signed RPM, source RPM, and checksums in **Releases** after publication.

T3 Code's in-app updater is disabled for this package. Keep the COPR repository enabled and use your normal Fedora updates, or run the command below to update just T3 Code.

```bash
sudo dnf upgrade --refresh t3code
```

### Install updates automatically

If you want Fedora to install updates without a manual DNF command, install the [DNF5 Automatic plugin](https://packages.fedoraproject.org/pkgs/dnf5/dnf5-plugin-automatic/).

```bash
sudo dnf install dnf5-plugin-automatic
sudoedit /etc/dnf/automatic.conf
```

Set these options in the existing `[commands]` section, or add that section if it is missing.

```ini
[commands]
upgrade_type = default
apply_updates = yes
```

Enable the timer. These settings install applicable updates from **all enabled repositories**, including COPR.

```bash
sudo systemctl enable --now dnf5-automatic.timer
systemctl list-timers dnf5-automatic.timer
```

See the [DNF5 Automatic documentation](https://dnf5.readthedocs.io/en/stable/dnf5_plugins/automatic.8.html) for scheduling and configuration options.

## Package differences

- The desktop app keeps Chromium sandboxing enabled. If you are troubleshooting display issues, try `t3code --ozone-platform=x11` or `t3code --ozone-platform=wayland`.
- The build excludes the Cursor SDK and Claude Agent SDK. Features that require those SDKs are unavailable.
- Electron uses its Chromium-branded FFmpeg build with proprietary codecs disabled. Some media formats supported by the upstream build may be unavailable.

## Remove

```bash
sudo dnf remove t3code
sudo dnf copr disable krnya/t3code
```

Removing the package leaves your T3 Code user data on disk. Disabling COPR is optional.

## Licenses and maintenance

The [packaging code](LICENSE) and [upstream T3 Code v0.1.10](https://github.com/pingdotgg/t3code/blob/v0.1.10/LICENSE) are MIT-licensed. The RPM includes the upstream T3 Code license and the Electron and Chromium license notices. The corresponding sources for the included FFmpeg library accompany the RPM release and are included in the source RPM.

Report Fedora packaging or update issues in this repository. Report application issues to [upstream T3 Code](https://github.com/pingdotgg/t3code/issues).

[Build architecture](docs/architecture.md)
