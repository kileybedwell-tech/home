# MacBook purchase (Sep 2026)

Decision notes from a claude.ai chat, pasted in 2026-09-24. Arrives 2026-09-25.

## The machine
14" MacBook Pro, M5 (10-core CPU/GPU), 32GB unified memory, 1TB SSD,
Space Black, $2,399, bought from Apple as build-to-order (quoted delivery Sep 23–30).

Why: Claude runs server-side, so the laptop only needs to handle containers,
language servers, browsers, test suites and parallel agent sessions. RAM is
the spec that matters (24GB floor, 32GB comfortable). Memory and storage are
soldered, so there are no upgrades later.

Rejected: M5 Pro 48GB ($3,099, +$700 for cores that won't be felt), M5 Max
($3,599+), MacBook Air (~$1,600, throttles, dimmer), M5 24GB ($2,199,
fallback only), Mac mini M4 as second machine ($799, the M1 covers it).

## Old M1 MacBook Pro 13" (Late 2020): kept, not traded in
Apple offered $295 in trade-in credit. It is being kept on purpose as the always-on Remote
Control host (worth ~$500 more than trade-in vs buying a $799 Mac mini).
Order matters: nothing on the M1 gets erased until the new Mac has
everything and has its own Time Machine backup.
- [x] Update macOS on the M1 (done 2026-09-25)
- [x] Migration Assistant from the M1 during the new Mac's setup (done
  2026-09-26). The new Mac first had to install a macOS update to match the
  M1; setup's "Finding Update" failed once, then the download ran overnight.
- [ ] Use the new Mac for a week or two; confirm photos, documents, eBay
  photo folders, passwords, bookmarks all came over
- [x] First Time Machine backup of the new Mac (WD My Passport), done 2026-09-26 (~25 min)
- [ ] On the M1: sign out of iCloud (turns off Find My / Activation Lock)
- [ ] Erase the M1: System Settings > General > Transfer or Reset > Erase
  All Content and Settings. The only step that can't be undone.
- [ ] Install Claude Code, log in
- [ ] Configure Remote Control (`claude remote-control` in the working folder)
- [ ] Leave it plugged in with sleep disabled

## The Apple order (checked 2026-09-24 from the order-details PDF)
- Order W1590899706, placed 2026-08-22, arriving Fri 2026-09-25.
- Only two items: the MacBook Pro and AppleCare+. **No external SSD was ordered.**
- Paid with Apple Card Monthly Installments: $2,399 at 0% APR, $199.91/mo for 12 months.
- Tax and recycling fee ($177.93) went on the Apple Card as a regular charge,
  not part of the 0% plan, so pay it off with the normal statement to avoid interest.

## Accessories
- [x] Backup drive: ordered a WD My Passport 2TB portable hard drive, $150,
  on 2026-09-25. Soldered storage means no recovery without a backup.
  Went with a spinning hard drive instead of an SSD because of the 2026
  memory-chip shortage: the Samsung T7 Shield 2TB SSD was ~$500 (vs ~$168 in
  2025), and speed doesn't matter for Time Machine. 2TB = ~2x the laptop's
  1TB, room for older versions.
- [x] Time Machine set up on the WD 2026-09-26. The first plug-in only showed
  macOS's "Allow accessory?" prompt; the disk was then added by hand in
  System Settings > General > Time Machine. Eject before unplugging.

## iDPRT SP410 label printer (fixed 2026-09-26)
After migration it showed "not compatible", then "Error" whenever a job was
sent (idle otherwise). Fix: remove the migrated printer, install the current
Mac driver from idprt.com (SP410 downloads page), then install Rosetta
(`softwareupdate --install-rosetta --agree-to-license`): the driver's
filter needs it on the new Mac. macOS re-adds the printer on its own when
USB is plugged back in. Print labels at 4x6; the Mac's default paper size
stays US Letter for the other printers.
- Maybe later: external monitor, after trying the 14" bare.
- Skip: hubs, stand, extra USB-C cables (3x Thunderbolt 5, HDMI, SDXC and
  headphone jack are built in).

## AppleCare+
$10.49/mo subscription (~$378 over 3 years). Can be dropped within the first
month if unnecessary. It does not end on its own: cancel it by hand if the
machine is ever sold.

## Other savings (moot now unless returning)
Apple Certified Refurbished ($230–450 below new, not configurable),
employer purchase program, Apple's veterans/military discount.
