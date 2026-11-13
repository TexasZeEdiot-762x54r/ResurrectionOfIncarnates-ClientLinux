# Rise of Incarnates experimental patch: Linux / Proton

Use a native Steam installation on x86-64 Linux and a user-owned Linux filesystem.
Install 64-bit Python 3.10+ with venv/pip. Steam Flatpak, non-Steam shortcuts and
Steam Deck are not verified by this port. Don't run the client or installer with sudo.

**Proton test target:** [GE-Proton11-11](https://github.com/GloriousEggroll/proton-ge-custom/releases/tag/GE-Proton11-11).
Install the tool in Steam's compatibility tools directory according to its release
instructions, restart Steam, then force it in the actual game's Properties >
Compatibility. An earlier Valve Proton Experimental test reached the menu but
failed to decode character-selection video; GE-Proton11-11 is the recommended
friend-test target, not a claim this new build has completed a Linux match.

1. While signed into Steam open `steam://install/258160`. Accept the free game's
   installation; confirm that it appears in your library. A tester reported that
   claiming on a new account worked. Use Properties > Installed Files > Browse to
   locate the game. Do not use a shortcut in place of a Steam entitlement.
2. Open `steam://open/console` and run
   `download_depot 258160 258162 1099989411347492672`. Wait for completion and
   note the path. You need the normal Steam executable/browser **and** the full
   assets. To rebuild from the complete offline recovery copy, replace both
   `--steam` and `--depot` and their paths with `--base /path/to/Original`.
   This replaces the asset download, not the Steam login for VPN or Internet play.
3. From a fresh checkout:

```bash
python3 tools/verify_release.py
python3 setup_client.py --mode internet \
  --steam "$HOME/.local/share/Steam/steamapps/common/Rise_of_Incarnates" \
  --depot "$HOME/.local/share/Steam/steamapps/content/app_258160/depot_258162"
chmod +x Play.sh
```

4. Set the game's Steam launch options to `"/absolute/path/to/ClientLinux/Play.sh" %command%`.
   Click Play **in Steam**. The launcher runs the debugger in Windows Python under
   Proton and the profile service/tunnel in Linux. It does not replace your Steam
   executable. Don't commit credentials, logs, prefixes, or generated config.

Internet defaults to **riseofincarnates.artificerreviver.com:52714** with its
certificate pin. For private Steam play use `--mode vpn --host PRIVATE_GATEWAY
--pin OPERATOR_CERTIFICATE_SHA256` after connecting the VPN (WireGuard recommended).
Neither mode sends Steam passwords to a game operator.

For a LAN account setup, choose `--mode lan --host PRIVATE_IPV4 --pin OPERATOR_PIN`.
Run `python3 tools/lan_login.py` and enter the username/password issued by your
server operator. LAN never falls back to Steam or the shared Internet server.

**Not yet verified:** Steam-free LAN peer battles, synchronized multiplayer
Lamina, Windows-Python account registration under Proton, media playback and a
full post-battle receipt on this exact patch. Do not describe these as supported
merely because setup or a Linux lobby test succeeds.

## Client updates

The launcher checks the signed development feed before play in public Internet mode.
Accept the prompt to install or decline to play the current build. LAN and private
VPN configurations make no public update request. To check manually run
`python roi_update.py --check` (Linux: `python3`); use `--apply` to install.
On Linux install OpenSSH client tools and zenity or kdialog for a desktop prompt.
On Windows use the Windows OpenSSH Client optional feature. Updates do not
download Steam's original game or change account credentials.
