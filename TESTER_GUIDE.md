# WA Outreach — Tester Install Guide

## Mac

### Install
1. Download `WA_Outreach_Mac.zip`
2. Double-click to unzip → you get `WA Outreach.app`
3. Drag `WA Outreach.app` into your **Applications** folder

### First launch (Gatekeeper bypass — one time only)
Because the app isn't yet signed with an Apple certificate, macOS will block it the first time.

**Fix:**
1. In Finder, **right-click** `WA Outreach.app` → **Open**
2. A dialog appears: click **Open** again
3. From now on, double-click works normally

> If you see "damaged and can't be opened", run this in Terminal once:
> ```
> xattr -cr "/Applications/WA Outreach.app"
> ```

### First launch (Chromium download)
On the very first launch, the app downloads Playwright's Chromium browser (~150 MB) in the background. This is what drives the WhatsApp Web sessions. The app is fully usable while this downloads — you only need Chromium once you add a WhatsApp number.

---

## Windows

### Install
1. Download `WA_Outreach_Windows.zip`
2. Unzip it anywhere (e.g. `C:\WA Outreach\`)
3. Open the folder and run `WA Outreach.exe`

### First launch (SmartScreen bypass — one time only)
Windows may show "Windows protected your PC".

**Fix:**
1. Click **More info**
2. Click **Run anyway**

### WebView2 requirement
The app needs Microsoft WebView2 (the browser engine for the UI window). It comes pre-installed on Windows 11 and recent Windows 10 machines. If the window doesn't open, download the WebView2 Runtime from:
https://developer.microsoft.com/microsoft-edge/webview2/ (Evergreen Bootstrapper, ~2 MB)

### First launch (Chromium download)
Same as Mac — ~150 MB download on first run, happens once in the background.

---

## Using the app

1. **Sign in** with your `@drivenproperties.com` account
   - First user: create an account from the login screen, it'll be admin
2. **Numbers** → Add a WhatsApp number → scan the QR code with your phone (same as WhatsApp Web)
3. **Campaign** → Import your contacts Excel/CSV → Start Sending
4. **Inbox** → See replies from contacts, reply back

## Known limitations in this test version
- Each person runs their own copy of the app with their own data
- WhatsApp sessions are stored locally on your machine
- No cloud sync between team members yet

## Feedback
Send issues or feedback to kareem.mazhar@drivenproperties.com
