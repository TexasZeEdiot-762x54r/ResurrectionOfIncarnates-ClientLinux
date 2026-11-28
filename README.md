# Rise of Incarnates Resurrection R5P

Public release based on the verified R70W Workshop gameplay. Public and Workshop share progression but never matchmaking.

Automatic log upload is disabled. After closing the game, run `Report-Bug.cmd` (Windows) or `bash Report-Bug.sh` (Linux; also works when invoked from fish). Use LaminaBot `/bugreport` in the ROI Resurrection Discord for a private submission code. The script collects only the last launch, prompts for missing SteamID/OS details and your bug summary, and asks before uploading. Your summary and Discord mention appear in the issue thread; logs and SteamID stay private to maintainers.

Do not mix this folder with a Workshop installation. The signed updater uses internal revision 4 for R5P to preserve compatibility with previous public feed revisions.

Updates come only from the public R#P channel (`stable/windows` or `stable/linux`). Workshop R#W updates never apply to this installation.

**This repository is a patch and launcher, not the original game.** A fresh
installation needs Steam ownership, the normal Steam installation, and all
verified original assets. Choose one of the two asset routes below.

## 1. Prerequisites

- Native Steam, signed in to the Steam account you will play with. Steam Flatpak,
  non-Steam shortcuts, and Steam Deck are not verified by this launcher.
- x86-64 Linux and 64-bit Python 3.12 or newer, with `venv` and `pip` available.
  Python 3.12 is the current test target. Examples below use `python3`; if that
  command is older than 3.10, use an installed `python3.12` instead in every command.
- OpenSSH client tools, including `ssh-keygen`, for signed update verification.
  Install `zenity` or `kdialog` for desktop update prompts.
- [GE-Proton11-11](https://github.com/GloriousEggroll/proton-ge-custom/releases/tag/GE-Proton11-11).
  Extract its release archive into your native Steam installation's
  `compatibilitytools.d` directory, then completely restart Steam. A common path
  is `~/.local/share/Steam/compatibilitytools.d/GE-Proton11-11/`. See the
  [upstream installation instructions](https://github.com/GloriousEggroll/proton-ge-custom#installation).
- Working GPU acceleration and Vulkan drivers appropriate for your GPU,
  including the 32-bit compatibility libraries required by Steam/Proton.
  A software-rendered RDP desktop is not a gaming-performance test.
- A fresh, user-owned Public folder on a Linux filesystem. Keep it separate
  from the original Steam game, the downloaded depot, and any existing Public
  install. Do not run Steam, setup, or the launcher as root.

Allow disk space for the Steam installation, downloaded asset depot (Route A),
a separate assembled `game/` directory, and the Proton/runtime dependencies.
Setup copies and patches into the Public folder; it does not patch the inputs.
An Internet connection is needed for setup dependencies, signed updates, Steam,
and the public game service. Do not expose loopback ports 52711/52713 publicly.

Check your Python version before continuing:

```bash
python3 --version
```

## 2. Claim and install the actual Steam game

In your Linux desktop terminal, open:

```bash
xdg-open 'steam://install/258160'
```

Accept the installation of **Rise of Incarnates** in Steam and wait for it to
finish. If a new account does not yet have the free app in its library, open
Steam's console:

```bash
xdg-open 'steam://open/console'
```

In the **Steam console**, not the Linux terminal, run:

```text
app_license_request 258160
```

Then retry `steam://install/258160`. This requests the legitimate free license;
it does not bypass Steam ownership or depot permissions. If Steam refuses the
license, stop and resolve that with your account before continuing.

In Steam, open the game's **Properties > Installed Files > Browse**. Record the
actual folder containing `exe/roi.exe`. One common default is:

```text
/home/YOUR_USER/.local/share/Steam/steamapps/common/Rise_of_Incarnates
```

Use your own library path if it differs. Keep the actual Steam library item
installed even when using Route B; a complete asset copy does not replace
Steam ownership, the installed library item, or Steam authentication.

## 3. Choose the original-asset route

| What you have | What to use |
| --- | --- |
| A fresh or ordinary Steam installation; no verified complete original copy | Route A: normal Steam folder **plus** the asset depot |
| A complete, unmodified original game/recovery folder that passes the packaged manifest | Route B: `--base`; no separate depot download needed |
| An already patched Public/official revival `game/` folder | Neither: obtain clean original inputs instead |

The patch installer verifies the original file sizes and SHA-256 hashes against
`patch/base-manifest.json`. A folder merely containing `exe/roi.exe` and
`resource/` is not proof that it is complete. A normal Steam download alone is
not assumed to contain every asset required by this patch.

### Route A: Download the asset depot

With native Steam signed in, open its console as described above. Enter this
exact command in the **Steam console**:

```text
download_depot 258160 258162 1099989411347492672
```

The numbers are the app, asset depot, and required depot manifest. Do not omit
the manifest or substitute a newer/different one. Wait for Steam to report that
the depot download is complete; the console prints its destination.

A common location is:

```text
/home/YOUR_USER/.local/share/Steam/steamapps/content/app_258160/depot_258162
```

Steam installations using `~/.steam/steam` or a different library may use a
different path. Trust the completed download's printed destination, not the
example. Pass the `depot_258162` folder itself, not its parent or a guessed
`resource/` subdirectory. Do not copy the depot over the Steam installation.

If Steam reports access denied, an unavailable manifest, or a failed download,
do not continue with partial files. Confirm the account owns the app and the
download completed, or use a verified complete original copy via Route B.

### Route B: Use a complete original game folder

Skip the depot download only if you already have all the required, unmodified
original files in one folder. It must contain `exe/` and `resource/` directly.
This may be a previously assembled clean recovery directory, or a complete
original copy that passes the manifest checks.

Do not use an existing patched Public folder as the base, and do not copy
another player's account credentials or save/profile files. If setup rejects
an original-file hash or reports a missing file, the folder is not a valid
complete base for this patch. Obtain the correct clean inputs; do not disable
verification or edit the manifest.

## 4. Install into a fresh Public folder

Clone this repository or extract its release archive into a new folder, for
example `~/Games/RiseOfIncarnatesPublic`. Open a terminal **in that folder**.
Verify the package first:

```bash
python3 tools/verify_release.py
```

If verification fails, get a clean package rather than changing its hashes.
Then run **one** of these setup commands, replacing all example paths.

**Route A: Steam installation plus completed depot download**

```bash
python3 setup_client.py --mode internet \
  --steam "$HOME/.local/share/Steam/steamapps/common/Rise_of_Incarnates" \
  --depot "$HOME/.local/share/Steam/steamapps/content/app_258160/depot_258162"
```

**Route B: Complete verified original folder**

```bash
python3 setup_client.py --mode internet \
  --base "/path/to/complete-clean-original-game"
```

`--base` replaces both `--steam` and `--depot`; do not combine them. In Route A,
both `--steam` and `--depot` are needed. Setup assembles a separate patched
`game/`, creates `.venv`, and obtains the pinned Windows Python/runtime libraries
for Proton. You do not need to install Windows Python manually.

Wait for setup to finish successfully, then run:

```bash
chmod +x Play.sh
```

If setup says to use a fresh checkout, do not run it over the existing install.
Use a new folder. Do not delete credentials/profiles to force setup to continue.
Keep the original inputs while diagnosing an incomplete setup.

## 5. Configure and launch through Steam

Open **Rise of Incarnates > Properties > Compatibility**, enable forcing a
specific compatibility tool, and select **GE-Proton11-11**. If it is missing,
check its extraction directory and completely restart Steam.

Under **General > Launch Options**, enter this as a single line, replacing the
username and path with this Public folder's actual absolute path:

```text
"/home/YOUR_USER/Games/RiseOfIncarnatesPublic/Play.sh" %command%
```

Keep the quotes around the script path and leave `%command%` outside them.
Use this package's `Play.sh`, not its generated `game/exe/roi.exe` directly.
Click **Play in Steam** on the actual game library item; do not create a
non-Steam shortcut or invoke the executable through standalone Wine.

The launcher checks signed Public updates, starts loopback-only Linux
profile/tunnel services, and runs the Windows debugger/Python inside Proton.
Leave Steam signed in. Enrollment uses your current Steam identity, never a
copied player's credentials. Check the in-game player name and footer after
reaching the menu. Distinct people testing together need distinct Steam accounts.

## 6. Updates and troubleshooting

Normal Internet-mode launches check the signed `stable/linux` feed.
Updates do not download the original Steam game/depot or replace Steam
ownership. Do not rerun setup to update an established install. With the game
closed, a manual check or apply is:

```bash
.venv/bin/python -I roi_update.py --check
.venv/bin/python -I roi_update.py --apply
```

An operator-managed hourly update timer is specific to the test host; it is not
installed by ordinary package setup. Keep Windows/Linux Public versions
compatible when testing multiplayer.

For a launcher/API check without starting the game, temporarily use this Steam
Launch Options line, then restore the normal one before playing:

```text
"/home/YOUR_USER/Games/RiseOfIncarnatesPublic/Play.sh" --doctor %command%
```

Common failures:

- **Missing original file/hash mismatch:** confirm the clean Steam and exact
  completed depot paths for Route A, or use a genuinely complete base for Route B.
- **`venv`/`pip` missing or Python too old:** install your distribution's matching
  Python/venv/pip packages, then use that Python executable for setup.
- **Signature check fails:** ensure OpenSSH client tools are installed and the
  system clock is correct. Do not bypass signature or TLS checks.
- **DXVK startup crash while painting an embedded browser:** the Linux test
  host needed the documented DXVK tightly-packed-image compatibility option.
  If you encounter this failure, try this single Steam Launch Options line:

  ```text
  DXVK_CONFIG="d3d11.disableDirectImageMapping = True" "/home/YOUR_USER/Games/RiseOfIncarnatesPublic/Play.sh" %command%
  ```

  This avoided the observed browser-copy startup crash on the test host; it is
  not a guarantee that every Linux GPU or battle is validated. See the
  [upstream DXVK option documentation](https://github.com/doitsujin/dxvk/blob/master/dxvk.conf).
- **Dark arena with a visible HUD:** the temporary WineD3D/OpenGL comparison
  showed this on the test host. Do not make `PROTON_USE_WINED3D=1` a permanent
  workaround without testing lighting and character previews. Prefer the
  intended Vulkan/DXVK path with a compatible GPU driver.
- **Low FPS over remote desktop:** distinguish game rendering from viewer
  performance and confirm the game uses the GPU, not software rendering. The
  private GPU viewer and driver configuration on l-codex101 are host-specific;
  ordinary setup does not install drivers, configure RDP, or reboot your PC.

## Diagnostics and validation scope

Relevant logs are under `tools/logs/`, including `play.log`, `proton-game.log`,
`proton-preflight.log`, and `public-tunnel.log`. Public builds do not automatically upload logs. Run `bash Report-Bug.sh` after closing the game to submit a report with your consent. Credential files,
passwords, tokens, and raw request payloads must not be shared or committed.

Keep generated configuration, credentials, profiles, prefixes, and logs out of
Git. Keep your own account files during updates; do not import another user's
profile. Verify character-selection media, 3D previews, arena lighting, a complete
battle, and results on your actual Linux/GPU combination. Successful setup or
reaching the menu alone does not prove multiplayer compatibility.
