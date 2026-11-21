# Mobile companion: UX specification

Part of the [mobile companion plan](mobile-companion-plan.md). Status: **Proposed**. This is the
specification the UX thread turns into mockups; no screen is built before Chris approves its mockup.

## Principles

- **Built for a phone, not a small desktop.** One task per screen, thumb-reachable actions at the
  bottom, no tables wider than the screen, no editing of code.
- **Always say which PC.** Every screen that can act shows the PC's name in its header, and every
  action button names it ("Cancel on CHRIS-PC").
- **Honest about what's possible.** Cancel appears only on runs that can cancel; there is no pause;
  an offline PC is shown as offline with when it was last seen, never as "starting soon".
- **Works on a bad connection.** Cached data shows at once, marked "Updated 2 min ago"; actions wait
  for the PC's answer and say so; nothing is shown as done before the PC confirms it.
- **KratosForge look.** The light shell, the app's own type and colors (`--brand-orange #f8991c` as
  the accent, from `app/ui/styles.css`) and the app's wording. Fantasy art only on the Profile's rank
  and achievement surfaces, never in controls, navigation or data. No decorative animation; progress
  bars and a spinner only.
- **Accessible.** WCAG 2.2 AA contrast; 44 × 44 pt touch targets; Dynamic Type / font scaling to
  200% without clipping; every state has text, not color alone; screen-reader labels on every
  control; reduced motion respected.

## Navigation

Bottom tab bar, five tabs: **Home**, **Jobs**, **Run**, **Optimize**, **Profile**. Devices is reached
from the PC switcher in every header and from Profile. Job details opens from Home or Jobs.

The PC switcher (header, top left) shows the current PC's name and a dot: green online, grey offline,
amber "connecting". Tapping it lists every paired PC with its state and "Pair another PC".

## Screens

### Home

- PC card: name, online state, last contact, connection path in small text ("On your network" or
  "Through relay"), memory and CPU bars.
- "Running now": up to 3 runs with name, progress, elapsed; "See all" goes to Jobs.
- "Needs attention": failed runs since last opened, low memory, security events.
- Empty states: "Nothing running on CHRIS-PC" with a Run button if anything is allowed; "No PCs yet"
  with Pair a PC.

### Jobs

- Segments: Running, Queued, Failed, Finished. Default Running if any, else Finished.
- Each row: kind icon (from the app's icon set), name, state text, elapsed or finished time, engine
  ("This PC", "Fabric").
- Pull to refresh; history covers 7 days, cached.

### Job details

- Header: name, state, PC name.
- Progress: bar with "Cell 4 of 12" or "Activity 3 of 7" where the run type reports it; elapsed;
  "Usually takes about 6 min" only when a past run of the same item exists.
- Metrics: memory and CPU of the PC during the run (not per run, said so).
- Log: last 200 lines, monospace, redacted, "Copy" button; error summary first when failed.
- Actions at the bottom: **Cancel on CHRIS-PC** (only if the run type can cancel), **Run again**
  (failed or finished runs of allowed items). Each asks for Face ID / fingerprint / passcode.
- Cancel shows "Asking CHRIS-PC to cancel..." until the PC reports Cancelled. For a Fabric job:
  "Fabric shows Cancelled once the job stops."

### Run (Execute)

- List of items the person marked "Allow from phone" on this PC, grouped by kind, with last result.
- Tapping one opens a sheet: item name, PC name, preset picker if presets exist (no free text),
  **Run on CHRIS-PC**. Biometric prompt, then "Sent. Waiting for CHRIS-PC..." until the ack, then
  opens Job details.
- Item changed since allowed: row disabled with "Changed since you allowed it. Allow it again on the PC."
- Empty: "Nothing is allowed to run from your phone yet. On your PC, open an item and choose Allow
  from phone."

### Optimize

- Savings summary with Measured, Estimated and Scenario figures kept apart (as `measurement.py`),
  Unknown shown as Unknown.
- Recommendations list, read-only, each with "Open on PC" (sends a `kratosforge://page/...` link to
  the PC only if the person taps it; P3).

### Devices

- Paired PCs: name (rename locally), state, last connected, connection path, what this phone may do,
  paired date. "Forget this PC" removes it on the phone and asks the PC to remove the pairing.
- "Pair a PC" opens the camera.

### Profile

- KratosForge identity (display name from the PC, no email), Academy progress, rank and achievements.
  Rank and achievement art per the visual style guide; the rest of the screen is plain.
- Settings: notification types, "Show PC name in notifications", app lock (require Face ID to open),
  "Remove all PCs".

## Desktop: Settings > Mobile companion (new page, mockup first)

- Switch: "Let phones connect to this PC" (off by default; disabled with the reason when policy turns
  it off).
- **Pair a phone** button: shows the QR for 120 s with a countdown, "Cancel", and the steps
  ("Open KratosForge Mobile, tap Pair a PC, scan this code").
- Approval dialog when a phone answers: phone name, platform, the 6-digit code with "Check this
  matches your phone", the choice "Monitor only" (default) or "Monitor and run allowed items",
  **Allow** (primary) and **Deny** (default style).
- Devices table: name (rename), platform, paired, last seen, path, permission, **Remove**.
- **Disconnect all phones**: ends current connections and pauses phone access; phones reconnect without pairing once phone access is switched back on.
- **Remove all phones**: deletes every pairing; each phone must pair again. Uses `confirmDialog` danger, as does **Remove** on a single device (a typed name only if policy calls for one).
- Allowed items: list with Remove; items are added from each item's own menu ("Allow from phone").
- Remote access (P2): relay status; when policy sets a relay URL it shows it read-only.

## User journeys

1. **First pairing:** PC Settings > Mobile companion > switch on > Pair a phone > phone Pair a PC
   > scan > both show 482 913 > Allow (Monitor only) > phone opens Home for CHRIS-PC. Target: under
   60 seconds, no typing.
2. **Check a long run from the couch:** notification "A run finished on your PC" > tap > biometric
   if app lock is on > Job details with result.
3. **Start a saved run away from the desk:** Run tab > "Daily sales load" > preset "Yesterday" >
   Run on CHRIS-PC > Face ID > Job details follows it.
4. **Cancel a runaway query:** Jobs > Running > query > Cancel on CHRIS-PC > Face ID > Cancelled.
5. **Lost phone:** on the PC, Settings > Mobile companion > Remove (or Disconnect all phones).
6. **Second PC:** PC switcher > Pair another PC > scan; switcher now lists both.

## Error states

| Situation | Message | Action |
| --- | --- | --- |
| PC offline | "CHRIS-PC is offline. Last seen 18:42. It may be asleep, closed or off the network." | Retry; cached data stays visible |
| Not on same network, no relay | "Your phone can't reach CHRIS-PC from this network. Join the same Wi-Fi or VPN, or ask IT about remote access." | Retry |
| KratosForge closed | "KratosForge isn't running on CHRIS-PC. Open KratosForge on CHRIS-PC, then try again." | Retry |
| QR expired | "This code expired. Show a new one on your PC." | Scan again |
| QR already used | "This code was already used. If that wasn't you, deny the request on your PC." | Scan again |
| Code mismatch | (person taps Deny) phone: "Pairing was denied on the PC." | Scan again |
| Device removed | "CHRIS-PC removed this phone. Pair again to reconnect." | Pair |
| Policy off | "Your organization has turned off phone access on CHRIS-PC. Ask your IT admin if you need it." | none |
| Not allowed | "This phone can only monitor CHRIS-PC. To run items from this phone, change its access in Settings > Mobile companion on CHRIS-PC." | none |
| Rate limited | "Too many requests. Try again in 30 seconds." | auto-retry |
| Start sent, no answer | "Not sure CHRIS-PC got this. Checking..." then the PC's real answer (same request id) | none |
| Run refused by a guard | The engine's own sentence, for example the Test/Prod refusal | none |

## Permission behavior on the phone

- Camera: asked when the person taps Pair a PC, with the reason first.
- Local network (iOS): asked at first direct connection: "To find your PC on this Wi-Fi."
- Notifications: off until the person turns them on in Profile > Settings (separate from pairing); the OS permission is asked then, never at install.
- Biometrics: asked at the first operational command.
- Denied camera: offer "Enter code instead" is **not** provided (the QR carries a 32-byte secret);
  show how to allow the camera in Settings.

## For the UX thread

Mockups needed (phone at 390 × 844 and 360 × 800, light theme, plus large text): Home, Jobs, Job
details (running, failed, cancelling), Run sheet, Devices, Profile, pairing scan and code screens,
and the desktop Settings > Mobile companion page with the QR and approval dialog.
